"""
Customer / Business-Partner EFRIS helpers.

verify_tin() — Query URA for a TIN and return the taxpayer's registered name,
               status and type.  Mirrors SAP B1 Business Partner → Verify TIN
               button (BusinessPartnerEfrisTabPage.cs → VerifyTin_Click).

SAP B1 flow:
  1. POST T119 with { "tin": "<TIN>" }
  2. Decrypt + parse:
       taxpayerName  / TaxpayerName / businessName
       taxpayerStatus / status
       taxpayerType   (INDIVIDUAL | COMPANY | GOVERNMENT | FOREIGN_NATIONAL)
  3. Return structured dict; caller maps taxpayerType → buyerType (B2B/B2C/…)

T-code T119 = Customer Information Query.
"""

import frappe

from efris_integration.api.base import EFRISClient, EFRISAPIError
from efris_integration.api.session import ensure_session, SESSION_ERRORS
from efris_integration.utils.helpers import log_efris


def buyer_type_for(taxpayer: dict) -> str:
    """SAP B1 BusinessPartner.cs: 201 → B2C; 202 + governmentTIN 1 → B2G; other 202 → B2B."""
    kind = str(taxpayer.get("taxpayerType") or "").strip()
    if kind == "201":
        return "B2C"
    if kind == "202":
        return "B2G" if str(taxpayer.get("governmentTIN") or "").strip() == "1" else "B2B"
    return ""


@frappe.whitelist()
def verify_customer(customer: str, company: str, update_name: int = 0) -> dict:
    """
    Verify one customer's TIN with URA (T119) and save what URA holds:
    Business Type (B2B/B2C/B2G) and EFRIS Invoice Name (= URA legal name);
    with update_name also the Customer Name. Customers without a TIN are
    skipped. Used by the EFRIS Customer TIN Verification report (SAP B1
    BulkBPVerify.cs).
    """
    from efris_integration.api.session import call_with_session

    tin = (frappe.db.get_value("Customer", customer, "tax_id") or "").strip()
    if not tin:
        return {"skipped": "no TIN"}
    client = EFRISClient(company=company)
    result = call_with_session(client, company, "T119", {"tin": tin, "ninBrn": ""}, "Customer", customer) or {}
    payer = result.get("taxpayer") or result
    legal = str(payer.get("legalName") or payer.get("businessName") or "").strip()
    btype = buyer_type_for(payer)
    if not btype:
        frappe.throw(f"URA returned an unknown taxpayer type '{payer.get('taxpayerType')}' for TIN {tin}.")

    values = {"efris_business_type": btype, "efris_buyer_type": btype}
    if legal:
        values["efris_invoice_name"] = legal
        if int(update_name or 0):
            values["customer_name"] = legal
    frappe.db.set_value("Customer", customer, values)
    return {"tin": tin, "business_type": btype, "legal_name": legal}


@frappe.whitelist()
def verify_tin(tin: str, company: str) -> dict:
    """
    Verify a TIN against URA EFRIS and return the registered taxpayer details.

    Returns:
        {
          "status":         "ok" | "not_found",
          "tin":            "1000012345",
          "taxpayer_name":  "ACME LIMITED",
          "taxpayer_type":  "COMPANY",          # raw URA field — JS maps to B2B/B2C/…
          "status_desc":    "Active",
          "message":        "human-readable summary"
        }
    """
    if not tin:
        frappe.throw("TIN is required.", title="Verify TIN")
    if not company:
        frappe.throw("Company is required.", title="Verify TIN")

    tin = tin.strip()
    client = EFRISClient(company=company)

    try:
        ensure_session(client, company)
    except Exception as exc:
        frappe.throw(str(exc), title="EFRIS Session Error")

    payload = {"tin": tin}

    try:
        result = client.call(
            "T119",
            payload=payload,
            encrypt=True,
            sign=True,          # T119 requires RSA signing — same as T130/T131
            doc_type="Customer",
            doc_name=tin,
        )
    except EFRISAPIError as exc:
        # Not-found or validation errors → surface cleanly
        log_efris("Customer", tin, "T119", payload, {}, "Failed",
                  f"[{exc.code}] {exc.message}")
        return {
            "status":  "not_found",
            "message": f"[{exc.code}] {exc.message}",
        }
    except Exception as exc:
        frappe.throw(str(exc), title="EFRIS Verify TIN Error")

    # ── Unwrap list wrapper if present ───────────────────────────────────────
    if isinstance(result, list) and result:
        result = result[0] if isinstance(result[0], dict) else {}
    if not isinstance(result, dict):
        result = {}

    # ── T119 response is nested: { "taxpayer": { "businessName": ..., ... } }
    # Drill into the inner object; fall back to top-level for older versions.
    taxpayer_obj = result.get("taxpayer") or result

    # ── Parse fields from the inner taxpayer object ───────────────────────────
    taxpayer_name = (
        taxpayer_obj.get("businessName")
        or taxpayer_obj.get("legalName")
        or taxpayer_obj.get("tradeName")
        or taxpayer_obj.get("shortName")
        or taxpayer_obj.get("taxpayerName")
        or taxpayer_obj.get("name")
        or ""
    )

    # taxpayerStatus is a code: "101" = Active, "102" = Cancelled
    _status_code = (
        taxpayer_obj.get("taxpayerStatus")
        or taxpayer_obj.get("status")
        or ""
    )
    status_desc = {"101": "Active", "102": "Cancelled"}.get(str(_status_code), _status_code or "Active")

    # taxpayerType is a code: "101"=Individual, "201"=Private Company, "202"=Govt
    _type_code = (
        taxpayer_obj.get("taxpayerType")
        or taxpayer_obj.get("customerType")
        or taxpayer_obj.get("businessType")
        or ""
    )
    # URA taxpayerType codes (from EFRIS API spec section 19)
    taxpayer_type = {
        "101": "INDIVIDUAL",
        "201": "COMPANY",
        "202": "COMPANY",          # Partnership / other private entity
        "203": "GOVERNMENT",
        "204": "FOREIGN_NATIONAL",
    }.get(str(_type_code), str(_type_code))

    returned_tin = taxpayer_obj.get("tin") or taxpayer_obj.get("TIN") or tin

    log_efris("Customer", tin, "T119", payload, result, "Success")

    return {
        "status":        "ok",
        "tin":           returned_tin,
        "taxpayer_name": taxpayer_name,
        "taxpayer_type": taxpayer_type,
        "status_desc":   status_desc,
        "message":       f"TIN {returned_tin} verified — {taxpayer_name or '(name not returned)'}",
        "raw_fields":    taxpayer_obj,   # inner taxpayer dict for debug display
    }
