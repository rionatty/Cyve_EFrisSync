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
            sign=False,
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

    # ── Parse response ────────────────────────────────────────────────────────
    # URA returns taxpayer details in the decrypted response body.
    # Field names vary by EFRIS version; try common variants.
    if isinstance(result, list) and result:
        result = result[0] if isinstance(result[0], dict) else {}

    # ── Parse response ────────────────────────────────────────────────────────
    # URA returns taxpayer details in the decrypted body.
    # Field names vary by EFRIS version; try all known variants (SAP B1 T119ResponseMapper).
    taxpayer_name = (
        result.get("taxpayerName")
        or result.get("TaxpayerName")
        or result.get("businessName")
        or result.get("BusinessName")
        or result.get("name")
        or ""
    )
    status_desc = (
        result.get("taxpayerStatus")
        or result.get("TaxpayerStatus")
        or result.get("status")
        or result.get("statusDesc")
        or "Active"
    )
    # taxpayerType: INDIVIDUAL | COMPANY | GOVERNMENT | FOREIGN_NATIONAL
    # JS maps this to B2C / B2B / B2G / B2F respectively
    taxpayer_type = (
        result.get("taxpayerType")
        or result.get("TaxpayerType")
        or result.get("customerType")
        or ""
    )
    returned_tin = result.get("tin") or result.get("TIN") or tin

    log_efris("Customer", tin, "T119", payload, result, "Success")

    if taxpayer_name or returned_tin:
        return {
            "status":        "ok",
            "tin":           returned_tin,
            "taxpayer_name": taxpayer_name,
            "taxpayer_type": taxpayer_type,
            "status_desc":   status_desc,
            "message":       f"TIN {returned_tin} verified — {taxpayer_name}",
        }

    # Response came back but had no usable fields
    return {
        "status":        "ok",
        "tin":           tin,
        "taxpayer_name": "",
        "taxpayer_type": "",
        "status_desc":   "Verified",
        "message":       "TIN verified with URA (no name returned).",
    }
