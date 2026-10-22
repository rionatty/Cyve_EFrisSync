"""
T109 — Submit AR Invoice (Sales Invoice) to EFRIS  [SAP B1: ARReserveInvoice.cs uses T109]
T106 — Query invoices

Mirrors: ARReserveInvoice.cs, UraARInvoice.cs, T109.cs, T106.cs

Pricing modes (controlled by EFRIS Settings):
  gross_on_ar_invoice  → unitPrice and total are VAT-inclusive (gross)
  price_on_ar_invoice  → include unitPrice field; otherwise send "0"
  use_t127             → fetch live excise rates from URA T127 before submission
"""

import json
import math
from datetime import datetime

import frappe

from efris_integration.api.base import EFRISClient, EFRISAPIError
from efris_integration.api.session import call_with_session
from efris_integration.utils.helpers import (
    generate_qr_code_b64,
    get_customer_efris_fields,
    get_efris_settings,
    get_item_efris_fields,
    map_tax_code,
    map_uom,
    resolve_branch_for_doc,
    safe_float,
    safe_str,
)
from efris_integration.excise import compute_excise_for_line, excise_by_line, line_key


# ─── T109: Submit Sales Invoice (AR Invoice) ──────────────────────────────

@frappe.whitelist()
def submit_sales_invoice(invoice_name: str) -> dict:
    doc = frappe.get_doc("Sales Invoice", invoice_name)

    if doc.docstatus != 1:
        frappe.throw("Invoice must be submitted before sending to EFRIS.")
    if doc.get("efris_invoice_id"):
        frappe.throw(f"Invoice already submitted to EFRIS (ID: {doc.efris_invoice_id}).")

    settings = get_efris_settings(doc.company)
    _validate_settings(settings)          # friendly error before hitting URA

    # ── Multi-branch: resolve URA Branch ID from the invoice's warehouse ──
    # When "Enable Multi-Branch" is checked in EFRIS Settings, the branch ID
    # is taken from the Warehouse master (URA section → URA Branch Id) rather
    # than from the global EFRIS Settings branch fields.
    # resolve_branch_for_doc() mutates settings.branch_id / branch_name so
    # that _seller_details() picks up the correct branch automatically.
    resolve_branch_for_doc(doc, settings)

    client = EFRISClient(company=doc.company)

    # Sync the resolved branch into the client's own settings copy so
    # make_global_info() sends the correct globalInfo.brn for this invoice.
    client.settings.branch_id   = settings.branch_id
    client.settings.branch_name = settings.branch_name

    # T127 prefetch if enabled
    if settings.use_t127:
        from efris_integration.api.t127 import get_industry_config
        try:
            get_industry_config(company=doc.company)
        except Exception as e:
            frappe.log_error(str(e), "EFRIS T127 prefetch failed — continuing without")

    buyer = get_customer_efris_fields(doc.customer)
    _check_buyer(doc, buyer, client)
    deemed_projects = _deemed_projects(doc, buyer, client) if _is_deemed(buyer) else None
    goods_details, tax_details, summary = _build_goods_and_tax(
        doc, settings,
        company=doc.company,
        deemed_projects=deemed_projects,
    )

    # is_export: Customer EFRIS tab → Export field = "Y - Yes"
    is_export = (buyer.get("efris_export") or "").startswith("Y")

    payload = {
        "sellerDetails":    _seller_details(settings, doc),
        "buyerDetails":     _buyer_details(doc, buyer),
        "basicInformation": _basic_info(doc, settings, invoice_type="1",
                                        is_export=is_export),
        "goodsDetails":     goods_details,
        "taxDetails":       tax_details,
        "summary":          summary,
        "payWay":           _pay_way(doc),
        "extend":           {"reason": "", "reasonCode": ""},
    }

    result = call_with_session(client, doc.company, "T109", payload, "Sales Invoice", invoice_name)

    # ── Log raw result so we can debug response structure ─────────────────
    frappe.logger("efris").info(
        f"[T109 raw result] {invoice_name}: "
        f"keys={list(result.keys()) if result else 'EMPTY_DICT'} | "
        f"preview={frappe.as_json(result)[:400]}"
    )

    _save_result(doc, result)

    # ── Recovery: if T109 returned no invoice details, look the invoice up ─
    # URA sometimes returns returnCode=00 but an empty response body.
    # The invoice IS registered — find it by our reference number (T106)
    # and read its details (T108).
    doc.reload()
    if not doc.get("efris_invoice_id"):
        try:
            _recover_from_ura(doc, client)
            doc.reload()
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"EFRIS recovery after T109 failed [{invoice_name}]"
            )

    # ── Success toast ──────────────────────────────────────────────────────
    doc.reload()
    _inv_id = doc.get("efris_invoice_id") or ""
    frappe.msgprint(
        f"✅ Invoice {invoice_name} submitted to EFRIS."
        + (f"<br>Invoice ID: <b>{_inv_id}</b>" if _inv_id else
           "<br><b>Note:</b> URA confirmed receipt but did not return invoice details yet. "
           "Click <b>Update EFRIS Details</b> in the EFRIS menu to fetch them."),
        indicator="green",
        title="EFRIS Submission",
    )
    return result


# ─── Fetch & save URA details for an already-submitted invoice ───────────────

@frappe.whitelist()
def fetch_invoice_from_ura(invoice_name: str) -> dict:
    """
    "Update EFRIS Details": refill FDN / Invoice ID / verification code / QR
    for an invoice URA already has. Mirrors SAP B1 btnUpdQR (T106 by
    reference number → T108).
    """
    doc = frappe.get_doc("Sales Invoice", invoice_name)
    client = EFRISClient(company=doc.company)
    if _recover_from_ura(doc, client):
        doc.reload()
        return {"status": "ok", "invoice_id": doc.get("efris_invoice_id") or "",
                "fdn": doc.get("efris_fdn_number") or ""}
    return {"status": "not_found",
            "message": f"URA has no invoice with reference {invoice_name} yet. "
                       "If you just uploaded it, try again in a few moments."}


# ─── T106: Query Invoices ─────────────────────────────────────────────────

@frappe.whitelist()
def query_invoices(
    company: str,
    invoice_no: str = "",
    from_date: str = "",
    to_date: str = "",
    page: int = 1,
) -> dict:
    client = EFRISClient(company=company)
    payload = {
        "invoiceNo": invoice_no,
        "startDate": from_date,
        "endDate": to_date,
        "pageNo": str(page),
        "pageSize": "20",
    }
    return client.call("T106", payload, encrypt=True, sign=True,
                       doc_type="Sales Invoice", doc_name="Query")


# ─── Recovery: find our invoice on URA (T106) and read its details (T108) ────

def _recover_from_ura(doc, client: "EFRISClient") -> bool:
    """
    Find this invoice on URA by the reference number we sent
    (sellerDetails.referenceNo = ERPNext name), then read its full details
    with T108 and save them. Only an exact reference match is accepted —
    never "the latest invoice", which could belong to another document.
    Returns True when the invoice was found and saved.
    """
    from frappe.utils import add_days, getdate, today

    start = add_days(getdate(doc.posting_date), -7)
    result = call_with_session(client, doc.company, "T106", {
        "referenceNo": doc.name,
        "queryType":   "1",
        "invoiceType": "1",
        "startDate":   str(start),
        "endDate":     str(today()),
        "pageNo":      "1",
        "pageSize":    "99",
    }, "Sales Invoice", doc.name)

    matches = [
        r for r in _extract_invoice_list(result)
        if isinstance(r, dict)
        and str(r.get("referenceNo") or (r.get("sellerDetails") or {}).get("referenceNo") or "").strip() == doc.name
        and str(r.get("isInvalid") or "0") != "1"
    ]
    if not matches:
        frappe.logger("efris").info(f"[recovery] {doc.name}: no URA invoice with this reference")
        return False

    fdn = str(matches[0].get("invoiceNo") or (matches[0].get("basicInformation") or {}).get("invoiceNo") or "").strip()
    if not fdn:
        return False
    detail = call_with_session(client, doc.company, "T108", {"invoiceNo": fdn}, "Sales Invoice", doc.name)
    if not (detail or {}).get("basicInformation"):
        return False
    _save_result(doc, detail)
    return True


# ─── URA stock check (T128) — warns, never blocks (SAP B1 btnURAStk) ──────────

@frappe.whitelist()
def check_ura_stock(invoice_name: str) -> dict:
    """
    Compare what this invoice sells with the stock URA holds (T128, per branch
    when multi-branch is on). Returns one row per stock item; `shortage` is
    True when URA holds less than the invoice needs.
    """
    doc = frappe.get_doc("Sales Invoice", invoice_name)
    settings = get_efris_settings(doc.company)
    client = EFRISClient(company=doc.company)

    needed: dict = {}
    for line in doc.items:
        if not frappe.db.get_value("Item", line.item_code, "is_stock_item"):
            continue
        branch = ""
        if settings.multi_branch and line.get("warehouse"):
            branch = frappe.db.get_value("Warehouse", line.warehouse, "efris_branch_id") or ""
        key = (line.item_code, branch)
        needed[key] = needed.get(key, 0.0) + abs(safe_float(line.get("stock_qty") or line.qty))

    rows = []
    for (item_code, branch), qty in needed.items():
        row = {"item_code": item_code, "branch_id": branch, "needed": qty,
               "ura_stock": None, "shortage": 0.0, "note": ""}
        goods_id = (frappe.db.get_value("Item", item_code, "efris_reference_no") or "").strip()
        if not goods_id:
            row["note"] = "Not registered on EFRIS (no URA goods ID)"
        else:
            payload = {"id": goods_id}
            if branch:
                payload["branchId"] = branch
            try:
                result = call_with_session(client, doc.company, "T128", payload, "Sales Invoice", doc.name) or {}
                row["ura_stock"] = safe_float(result.get("stock"))
                row["shortage"] = round(max(0.0, qty - row["ura_stock"]), 6)
            except EFRISAPIError as exc:
                row["note"] = f"[{exc.code}] {exc.message}"
        rows.append(row)

    return {
        "rows": rows,
        "shortage": any(r["shortage"] > 0 for r in rows),
        "problems": any(r["note"] for r in rows),
    }


# ─── Buyer checks before upload (SAP B1 ARInvoice.cs) ─────────────────────────

def _check_buyer(doc, buyer: dict, client: "EFRISClient") -> None:
    """
    Business type must be set; B2B needs a TIN; for B2B/B2G the TIN and legal
    name must match what URA holds (T119), otherwise URA would fiscalise the
    invoice to the wrong buyer.
    """
    btype = (buyer.get("efris_business_type") or buyer.get("efris_buyer_type") or "").strip().upper()
    if not btype:
        frappe.throw(
            f"Set <b>Business Type</b> on the EFRIS tab of customer <b>{doc.customer}</b> "
            "(B2B, B2C, B2G or B2F) before uploading.",
            title="EFRIS: Business Type Missing",
        )
    tin = (buyer.get("tax_id") or "").strip()
    if btype == "B2B" and not tin:
        frappe.throw(
            f"Customer <b>{doc.customer}</b> is B2B but has no <b>Tax ID (TIN)</b>.",
            title="EFRIS: Customer TIN Missing",
        )
    if btype not in ("B2B", "B2G") or not tin:
        return

    result = call_with_session(client, doc.company, "T119", {"tin": tin, "ninBrn": ""},
                               "Sales Invoice", doc.name) or {}
    payer = result.get("taxpayer") or result
    ura_tin = str(payer.get("tin") or "").strip()
    ura_name = str(payer.get("legalName") or "").strip()
    if ura_tin and ura_tin != tin:
        frappe.throw(
            f"URA returned TIN <b>{ura_tin}</b> for customer TIN <b>{tin}</b>. Check the customer's Tax ID.",
            title="EFRIS: Customer TIN Mismatch",
        )
    names = {_norm_name(n) for n in (doc.customer_name, buyer.get("customer_name"), buyer.get("efris_invoice_name")) if n}
    if ura_name and _norm_name(ura_name) not in names:
        frappe.throw(
            f"Customer <b>{doc.customer_name}</b> does not match the legal name URA holds for TIN "
            f"<b>{tin}</b>: <b>{ura_name}</b>.<br>"
            "Open the customer and use <b>Verify TIN</b> to update its name, then upload again.",
            title="EFRIS: Customer Name and TIN Don't Match",
        )


def _norm_name(name: str) -> str:
    """Compare names ignoring case, punctuation and extra spaces."""
    import re
    return " ".join(re.sub(r"[^A-Z0-9 ]", " ", str(name).upper()).split())


# ─── Deemed VAT (T137 projects) ───────────────────────────────────────────────

def _is_deemed(buyer: dict) -> bool:
    return str(buyer.get("efris_deemed_vat") or "").upper().startswith("Y")


def _deemed_projects(doc, buyer: dict, client: "EFRISClient") -> dict:
    """URA deemed-VAT project per goods category for this buyer (T137)."""
    tin = (buyer.get("tax_id") or "").strip()
    if not tin:
        frappe.throw(
            f"Customer <b>{doc.customer}</b> is set to Deemed VAT but has no Tax ID (TIN).",
            title="EFRIS: Deemed VAT",
        )
    projects = {}
    for line in doc.items:
        category = get_item_efris_fields(line.item_code).get("efris_goods_category_id") or "238"
        if category in projects:
            continue
        result = call_with_session(client, doc.company, "T137",
                                   {"tin": tin, "commodityCategoryCode": category},
                                   "Sales Invoice", doc.name) or {}
        found = result.get("deemedAndExemptProjectList") or []
        if not found:
            frappe.throw(
                f"URA has no deemed VAT project for TIN <b>{tin}</b> and goods category "
                f"<b>{category}</b> (item {line.item_code}). Ask the customer to register the project with URA.",
                title="EFRIS: No Deemed VAT Project",
            )
        projects[category] = found[0]
    return projects


def _extract_invoice_list(result: dict) -> list:
    """
    Normalise a T106 response (many possible shapes) to a flat list of
    invoice record dicts.  Returns an empty list if nothing useful found.
    """
    if isinstance(result, list):
        return result

    if not isinstance(result, dict):
        return []

    # Common T106 response keys seen in the wild
    for key in ("invoiceList", "data", "list", "records", "invoices"):
        v = result.get(key)
        if isinstance(v, list) and v:
            return v
        if isinstance(v, dict) and v.get("basicInformation"):
            return [v]

    # Some T-codes return a single invoice at the top level
    if result.get("basicInformation"):
        return [result]

    return []


# ─── Shared payload builders ─────────────────────────────────────────────────

def _validate_settings(settings) -> None:
    """
    Check all required EFRIS Settings fields before hitting URA.
    Throws a single user-friendly error listing every missing field
    and a direct link to the settings page.

    URA field requirements (error codes):
      1115 — sellerDetails.emailAddress cannot be empty
      1116 — sellerDetails.mobilePhone cannot be empty
      1xxx — tin / device_no / legal_name also required
    """
    missing = []

    if not (settings.tin or "").strip():
        missing.append("TIN")
    if not (settings.device_no or "").strip():
        missing.append("Device No")
    if not (settings.email or "").strip():
        missing.append("Email Address")
    if not (settings.phone or "").strip():
        missing.append("Phone")
    if not ((settings.legal_name or "") or (settings.company_name or "")).strip():
        missing.append("Legal Name / Company Name")

    if missing:
        frappe.throw(
            "The following required fields are missing in "
            "<b>EFRIS Settings</b> for company <b>{company}</b>:<br><br>"
            "<ul>{items}</ul>"
            'Please go to <a href="/app/efris-settings" target="_blank">'
            "EFRIS Settings</a> and fill them in before uploading.".format(
                company=settings.company or "",
                items="".join(f"<li>{f}</li>" for f in missing),
            ),
            title="EFRIS Settings Incomplete",
        )


def _seller_details(settings, doc) -> dict:
    # Branch ID priority:
    #   1. settings.branch_id  — already overwritten by resolve_branch_for_doc()
    #      when multi_branch is enabled (contains the warehouse's URA Branch Id)
    #   2. doc.efris_branch_id — legacy fallback (manually set hidden field)
    #   3. Empty string
    branch_id = (
        settings.branch_id
        or doc.get("efris_branch_id")
        or ""
    )
    return {
        "tin":                  settings.tin,
        "ninBrn":               settings.taxpayer_id,
        "passportNumber":       "",
        "legalName":            settings.legal_name or "",
        "businessName":         settings.company_name or "",
        "address":              settings.address or "",
        "mobilePhone":          settings.phone or "",
        "linePhone":            settings.phone or "",
        "emailAddress":         settings.email or "",
        "placeOfBusiness":      settings.address or "",
        "referenceNo":          doc.name,
        "branchId":             branch_id,
        "branchName":           settings.branch_name or "",
        "branchCode":           settings.branch_code or "",
        "isCheckReferenceNo":   "0",   # T109 API spec: "0" = do not validate ref uniqueness
    }


def _buyer_details(doc, buyer: dict) -> dict:
    """
    Map ERPNext Customer EFRIS fields to URA buyerDetails object.

    SAP B1 ARReserveInvoice.cs CreateInvoice() buyer type mapping:
      B2B / B2G → "0"   (business / government — Tax ID required)
      B2C       → "1"   (individual consumer)
      B2F       → "0"   (foreign/export — TIN empty; NOTE: SAP B1 has "2" commented out,
                          actual code uses "0": // str4 = "2"; str4 = "0";)
    """
    buyer_type_raw = (
        buyer.get("efris_business_type")
        or buyer.get("efris_buyer_type")
        or "B2C"
    ).strip().upper()

    # URA numeric buyer type — mirrors SAP B1 CreateInvoice() BuyerType logic exactly.
    # B2F maps to "0" NOT "2" — the "2" line is commented out in the working SAP B1 code.
    _TYPE_MAP = {"B2B": "0", "B2G": "0", "B2C": "1", "B2F": "0"}
    ura_buyer_type = _TYPE_MAP.get(buyer_type_raw, "1")

    # B2F (foreign) buyers must have an empty TIN (SAP B1: VatId = "" when BType="B2F")
    buyer_tin = "" if buyer_type_raw == "B2F" else safe_str(buyer.get("tax_id"))

    return {
        "buyerTin":           buyer_tin,
        "buyerNinBrn":        safe_str(buyer.get("efris_nin_brn")),
        "buyerPassportNum":   "",
        "buyerLegalName":     doc.customer_name or "",
        "buyerBusinessName":  doc.customer_name or "",
        "buyerAddress":       doc.address_display or "",
        "buyerEmail":         safe_str(buyer.get("email_id")),
        "buyerMobilePhone":   safe_str(buyer.get("mobile_no")),
        "buyerLinePhone":     safe_str(buyer.get("mobile_no")),
        "buyerPlaceOfBusi":   doc.address_display or "",
        "buyerType":          ura_buyer_type,            # "0", "1", or "2"
        "buyerCitizenship":   safe_str(buyer.get("efris_citizenship")) or "Ugandan",
        "buyerSector":        safe_str(buyer.get("efris_sector")) or "1",
        "buyerReferenceNo":   doc.get("po_no") or "",
        "nonResidentFlag":    "0",    # T109 API spec: "0" = resident buyer
        "deliveryTermsCode":  "",     # T109 API spec: delivery terms (empty = not specified)
    }


def _basic_info(
    doc,
    settings,
    invoice_type: str = "1",
    ori_invoice_id: str = "",
    is_export: bool = False,
) -> dict:
    """
    Build basicInformation block.

    SAP B1 (ARInvoice.cs):
      invoiceIndustryCode = "101"  for domestic invoices
      invoiceIndustryCode = "102"  when customer Export flag = "Y"
    """
    posting = doc.posting_date
    if hasattr(posting, "strftime"):
        issued_date = posting.strftime("%Y-%m-%d %H:%M:%S")
    else:
        issued_date = str(posting) + " 00:00:00"

    # "102" = export invoice; "101" = standard domestic invoice
    industry_code = "102" if is_export else (settings.industry_code or "101")

    return {
        "invoiceId":           "",
        "invoiceNo":           "",     # must be empty on upload (modeCode=1); URA assigns this
        "antifakeCode":        "",
        "deviceNo":            settings.device_no,
        "issuedDate":          issued_date,
        "operator":            frappe.session.user,
        "currency":            doc.currency or "UGX",
        "oriInvoiceId":        ori_invoice_id,
        "invoiceType":         invoice_type,   # "1"=normal, "2"=credit note
        "invoiceKind":         "1",            # "1"=normal, "2"=prepayment
        "dataSource":          "103",          # 103=ERP system
        "invoiceIndustryCode": industry_code,
        "vatApplicableFlag":   "1",    # "1" = VAT registered (always for EFRIS taxpayers)
        "isBatch":             "0",            # T109 API spec: "0" = not a batch invoice
    }


def _build_goods_and_tax(
    doc,
    settings,
    company: str = "",
    deemed_projects: "dict | None" = None,
) -> tuple[list, list, dict]:
    """
    Build goodsDetails, taxDetails, and summary for any invoice-like doc.

    Mirrors SAP B1 CreateInvoice():
      • unitPrice / total    → VAT-INCLUSIVE (gross), totals TRUNCATED to 2dp
      • taxRate              → "0.18", "0", "-"
      • tax category         → 01 standard, 02 zero, 03 exempt, 11 out of scope,
                               04 deemed (whole invoice), 05 excise (own row)
      • discounts            → item line (discountFlag 1, full price, discountTotal)
                               followed by a "(Discount)" line (discountFlag 0,
                               negative total); covers line discounts and the
                               invoice-level additional discount
      • deemed VAT customer  → deemedFlag 1, tax 0, item "(Deemed)", URA project
                               from T137 (deemed_projects, only when uploading)
      • out of scope ("00")  → vatApplicableFlag 0, taxRate "0", category 11
      • orderNumber          → 0-based, one per entry (items and discount lines)

    Read-only (no URA calls), so the print format can call it too.
    """
    goods_details: list[dict] = []
    tax_buckets: dict[str, dict] = {}
    total_net = total_tax = total_gross = 0.0
    _company = company or doc.company or ""
    buyer = get_customer_efris_fields(doc.customer) if doc.get("customer") else {}
    deemed = _is_deemed(buyer)
    tax_included_in_rate = _is_tax_included_in_rate(doc)
    invoice_discount = abs(safe_float(doc.get("discount_amount"))) > 0.0001
    # Excise from the invoice's excise tax row, per line: (amount, rate %)
    excise_map = excise_by_line(doc, settings)
    order_no = 0

    for line in doc.items:
        item_fields = get_item_efris_fields(line.item_code)
        uom_code = map_uom(line.uom, _company)
        tax_rate, cat_code, cat_name, ura_rate_str, _erp_tax = _resolve_line_tax(doc, line, _company)
        out_of_scope = cat_code == "11"
        qty = safe_float(line.qty)
        line_excise = excise_map.get(line_key(line), (0.0, 0.0))[0]
        excise_per_unit = line_excise / qty if qty else 0.0

        # ── Gross prices: (price + excise) × (1 + VAT) — VAT is charged on
        #    price + excise, and URA's line total includes both (SAP B1) ─────
        def gross(price):
            price = safe_float(price)
            if tax_included_in_rate:
                return round(price, 4)
            return round((price + excise_per_unit) * (1 + tax_rate / 100), 4)

        gross_unit_price = gross(line.rate)
        list_price = safe_float(line.get("price_list_rate"))
        full_unit_price = gross(list_price) if list_price > safe_float(line.rate) + 0.0001 else gross_unit_price

        full_total = _trunc2(qty * full_unit_price)
        if invoice_discount:
            # net_amount carries the invoice-level discount share (ex-VAT, ex-excise)
            paid_total = round((safe_float(line.net_amount) + line_excise) * (1 + tax_rate / 100), 2)
        else:
            paid_total = _trunc2(qty * gross_unit_price)
        discount = round(full_total - paid_total, 2)
        has_discount = discount >= 0.01

        # Deemed VAT: price stays as invoiced, but no VAT is charged on the line
        vat_rate = 0.0 if deemed else tax_rate
        vat_rate_str = "0" if (deemed or out_of_scope) else ura_rate_str

        def vat_of(amount):
            if vat_rate <= 0:
                return 0.0
            d = vat_rate / 100
            return math.copysign(_trunc2(abs(amount) * d / (1 + d)), amount)

        if has_discount:
            unit_price, total = full_unit_price, full_total
            tax_amount = vat_of(full_total)
            discount_tax = vat_of(-discount)            # negative
            line_gross = round(full_total - discount, 2)
            line_tax = round(tax_amount + discount_tax, 2)
        else:
            unit_price, total = gross_unit_price, paid_total
            tax_amount = vat_of(paid_total)
            discount_tax = 0.0
            line_gross = paid_total
            line_tax = tax_amount
        line_net = round(line_gross - line_tax, 2)

        total_net += line_net
        total_tax += line_tax
        total_gross += line_gross

        # ── Excise duty: the line's share of the invoice's excise tax row ────
        # (base = VAT-exclusive paid amount, which includes the excise)
        excise_data = compute_excise_for_line(line, item_fields, settings, doc.currency,
                                              excise=excise_map.get(line_key(line)))
        excise_tax_amount = round(excise_data.pop("_line_tax", 0.0), 2)
        registered_for_excise = bool(getattr(settings, "has_local_excise_duty", False))
        if excise_data.get("exciseFlag") == "1" and not registered_for_excise:
            excise_data = {
                "exciseFlag": "2", "exciseRate": "", "exciseRule": "",
                "exciseTax":  "",  "pack":        "", "stick":      "",
                "exciseUnit": "",  "exciseCurrency": "", "exciseRateName": "",
            }
        if excise_data.get("exciseFlag") == "1":
            if excise_tax_amount:
                excise_base = round(line_net - excise_tax_amount, 2)
                excise_data["exciseTax"] = str(excise_tax_amount)
                if "excise" not in tax_buckets:
                    tax_buckets["excise"] = {
                        "net": 0.0, "tax": 0.0, "gross": 0.0,
                        "rate":      excise_data["exciseRate"],
                        "rate_name": excise_data["exciseRateName"],
                        "excise_unit":     excise_data.get("exciseUnit", ""),
                        "excise_currency": excise_data.get("exciseCurrency", ""),
                    }
                tax_buckets["excise"]["net"]   += excise_base
                tax_buckets["excise"]["tax"]   += excise_tax_amount
                tax_buckets["excise"]["gross"] += line_net

        # ── Goods entry ──────────────────────────────────────────────────────
        goods_cat_id = item_fields.get("efris_goods_category_id") or "238"
        if excise_data.get("exciseFlag") == "1":
            excise_cat_id   = item_fields.get("efris_excise_category_id") or goods_cat_id
            excise_cat_name = item_fields.get("efris_excise_category_name") or line.item_group or ""
        else:
            excise_cat_id   = goods_cat_id
            excise_cat_name = line.item_group or ""

        item_name = (line.item_name or line.item_code) + (" (Deemed)" if deemed else "")
        vat_flag = "0" if out_of_scope else "1"
        good: dict = {
            "item":              item_name,
            "itemCode":          line.item_code,
            "qty":               str(qty),
            "unitOfMeasure":     uom_code,
            "unitPrice":         str(unit_price),
            "total":             str(total),
            "taxRate":           vat_rate_str,
            "tax":               str(tax_amount),
            "remainAmount":      str(line_gross),
            "orderNumber":       str(order_no),
            "discountFlag":      "1" if has_discount else "2",
            "deemedFlag":        "1" if deemed else "2",
            "vatApplicableFlag": vat_flag,
            "categoryId":        excise_cat_id,
            "categoryName":      excise_cat_name,
            "goodsCategoryId":   goods_cat_id,
            "goodsCategoryName": line.item_group or "",
            **excise_data,
        }
        if has_discount:
            good["discountTotal"] = str(-discount)
            good["discountTaxRate"] = vat_rate_str
        if deemed:
            project = (deemed_projects or {}).get(goods_cat_id) or {}
            good["deemedExemptCode"] = "101"
            if project:
                good["vatProjectId"] = str(project.get("projectId") or "")
                good["vatProjectName"] = str(project.get("projectName") or "")
        goods_details.append(good)
        order_no += 1

        if has_discount:
            goods_details.append({
                "item":              item_name + " (Discount)",
                "itemCode":          line.item_code,
                "unitOfMeasure":     uom_code,
                "total":             str(-discount),
                "taxRate":           vat_rate_str,
                "tax":               str(discount_tax),
                "remainAmount":      "0",
                "orderNumber":       str(order_no),
                "discountFlag":      "0",
                "discountTaxRate":   vat_rate_str,
                "deemedFlag":        "2",
                "exciseFlag":        "2",
                "vatApplicableFlag": vat_flag,
                "categoryId":        goods_cat_id,
                "categoryName":      line.item_group or "",
                "goodsCategoryId":   goods_cat_id,
                "goodsCategoryName": line.item_group or "",
            })
            order_no += 1

        # ── VAT tax bucket (deemed invoices use one 04 bucket) ───────────────
        key = "04" if deemed else cat_code
        if key not in tax_buckets:
            tax_buckets[key] = {
                "net": 0.0, "tax": 0.0, "gross": 0.0,
                "rate": "0" if (deemed or out_of_scope) else ura_rate_str,
                "name": "D: Deemed" if deemed else cat_name,
            }
        tax_buckets[key]["net"]   += line_net
        tax_buckets[key]["tax"]   += line_tax
        tax_buckets[key]["gross"] += line_gross

    # ── Tax details ─────────────────────────────────────────────────────────
    # Rule [1342]: netAmount + taxAmount MUST equal grossAmount exactly.
    # Excise duty gets its own row (05); taxRateName is mandatory there (2831).
    tax_details = []
    for code, v in tax_buckets.items():
        t_net   = round(v["net"],  2)
        t_tax   = round(v["tax"],  2)
        t_gross = round(t_net + t_tax, 2)
        if code == "excise":
            tax_details.append({
                "taxCategory":     "Excise Duty",
                "taxCategoryCode": "05",
                "netAmount":       str(t_net),
                "taxRate":         v["rate"],
                "taxAmount":       str(t_tax),
                "grossAmount":     str(t_gross),
                "exciseUnit":      v["excise_unit"],
                "exciseCurrency":  v["excise_currency"],
                "taxRateName":     v["rate_name"],
            })
        else:
            tax_details.append({
                "taxCategory":     v["name"],
                "taxCategoryCode": code,
                "netAmount":       str(t_net),
                "taxRate":         v["rate"],
                "taxAmount":       str(t_tax),
                "grossAmount":     str(t_gross),
                "exciseUnit":      "",
                "exciseCurrency":  "",
                "taxRateName":     _tax_rate_label(v["rate"], code),
            })

    # ── Summary ─────────────────────────────────────────────────────────────
    # Rule [1345]: summary.grossAmount = sum of taxDetails gross EXCEPT excise (05).
    s_net   = round(total_net,  2)
    s_tax   = round(total_tax,  2)
    s_gross = round(s_net + s_tax, 2)
    summary = {
        "netAmount":   str(s_net),
        "taxAmount":   str(s_tax),
        "grossAmount": str(s_gross),
        "itemCount":   str(len(goods_details)),
        "modeCode":    "1",
        "remarks":     doc.get("remarks") or "",
        "qrCode":      "",
    }

    return goods_details, tax_details, summary


def get_efris_print_data(doc) -> dict:
    """
    Jinja method for print formats: the goods lines, taxDetails and summary
    exactly as built for URA (T109), so the printed tax matches what URA holds
    even when the invoice has no ERPNext tax row (URA then defaults to 18%).
    Returns {} if EFRIS isn't configured for the company; the print format
    then falls back to ERPNext's own figures.
    """
    try:
        settings = get_efris_settings(doc.company)
        goods, taxes, summary = _build_goods_and_tax(doc, settings, company=doc.company)
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"EFRIS print data failed [{doc.name}]")
        return {}
    # Words must match the printed (URA) total, which can differ from ERPNext's
    # grand total when the invoice has no tax row.
    in_words = frappe.utils.money_in_words(safe_float(summary["grossAmount"]), doc.currency)
    return {"goods": goods, "taxes": taxes, "summary": summary, "in_words": in_words}


def _pay_way(doc) -> list:
    mode_map = {
        "Cash":         "101",
        "Bank":         "102",
        "Credit Card":  "103",
        "Mobile Money": "104",
        "Cheque":       "105",
    }
    mode = mode_map.get(doc.get("mode_of_payment") or "", "102")
    return [
        {
            "paymentMode":   mode,
            "paymentAmount": str(safe_float(doc.grand_total)),
            "orderNumber":   "1",
        }
    ]


def _is_tax_included_in_rate(doc) -> bool:
    """
    Return True if the invoice's VAT is already baked into item prices
    (ERPNext "Is this Tax Included in Basic Rate?" checkbox on tax row).

    When True:  line.rate = gross (VAT-inclusive) → do NOT multiply by (1+rate)
    When False: line.rate = net  (VAT-exclusive)  → multiply by (1+rate) to get gross
    """
    for tax in _vat_rows(doc):
        return bool(getattr(tax, "included_in_print_rate", 0))
    return False


VAT_CHARGE_TYPES = ("On Net Total", "On Previous Row Total", "On Previous Row Amount")


def _vat_rows(doc) -> list:
    """
    The invoice's VAT rows: percentage tax rows that are not the excise row
    (EFRIS Settings → Excise Duty Account). VAT may be "On Previous Row Total"
    when it is charged on price + excise.
    """
    excise_account = (frappe.get_cached_value("EFRIS Settings", doc.company, "excise_liability_account")
                      if doc.get("company") else "") or ""
    return [t for t in (doc.taxes or [])
            if t.charge_type in VAT_CHARGE_TYPES and (t.account_head or "") != excise_account]


def _trunc2(x: float) -> float:
    """
    Truncate to exactly 2 decimal places (floor, not round).

    URA rule [1316]: goodsDetails.total must equal trunc(qty × unitPrice, 2).
    Python's round() can differ by 0.01 due to banker's rounding — use floor.
    """
    return math.floor(x * 100) / 100


def _resolve_line_tax(doc, line, company: str) -> tuple:
    """
    Resolve URA tax info AND the ERPNext-computed tax amount for one line item.

    Lookup order for URA category:
      1. Find the first "On Net Total" tax row on the invoice
      2. Look its account_head up in EFRIS Tax Mapping → ura_tax_code
            "18"  → Standard 18%  → taxRate "0.18", category "01"
            "0"   → Zero-rated    → taxRate "0",    category "02"
            "-"   → Exempt        → taxRate "-",    category "03"
      3. Fall back to 18% if no mapping found

    Per-line tax AMOUNT:
      • Read from item_wise_tax_detail (ERPNext already computed it per line)
        JSON format: {"ITEM-CODE": [rate%, amount], ...}
      • Falls back to None (caller computes net × rate)

    Returns:
        (tax_rate_float, cat_code, cat_name, ura_rate_str, line_tax_amount_or_None)
    """
    for tax in _vat_rows(doc):
        tax_account = (tax.account_head or "").strip()

        # ── URA category from EFRIS Tax Mapping ─────────────────────────────
        ura_code = map_tax_code(tax_account, company) if tax_account else ""
        tax_rate_f, cat_code, cat_name, ura_rate_str = _ura_code_to_rate(ura_code)

        # ── Per-line amount from ERPNext item_wise_tax_detail ────────────────
        # ERPNext stores {"item_code": [rate_pct, computed_tax_amount], ...}
        line_tax_amount = None
        raw_detail = getattr(tax, "item_wise_tax_detail", None) or ""
        if raw_detail:
            try:
                detail = json.loads(raw_detail)
                # Key can be item_code; fall back to item_name
                for key in (line.item_code, getattr(line, "item_name", "")):
                    if key and key in detail:
                        row = detail[key]
                        if isinstance(row, (list, tuple)) and len(row) >= 2:
                            item_rate  = safe_float(row[0])
                            line_tax_amount = safe_float(row[1])
                            # If item has a different effective rate, override category
                            if item_rate != tax_rate_f:
                                tax_rate_f, cat_code, cat_name, ura_rate_str = \
                                    _ura_code_to_rate(str(int(item_rate)) if item_rate > 0 else "0")
                        break
            except Exception as exc:
                frappe.logger("efris").warning(
                    f"[_resolve_line_tax] item_wise_tax_detail parse error "
                    f"for {line.item_code}: {exc}"
                )

        return tax_rate_f, cat_code, cat_name, ura_rate_str, line_tax_amount

    # No On Net Total tax row found — default to standard 18%
    return 18.0, "01", "Standard", "0.18", None


def _ura_code_to_rate(ura_code: str) -> tuple:
    """
    Convert a URA tax code string to (rate_float, cat_code, cat_name, rate_str).

    Mapping (mirrors SAP B1 ARInvoice.cs):
      "18"  → Standard VAT 18%  → "01", "0.18"
      "0"   → Zero-rated        → "02", "0"
      "-"   → Exempt            → "03", "-"
      "00"  → Out of scope      → "11", "0"   (goods vatApplicableFlag "0")
      ""    → fallback to 18%   → "01", "0.18"
    """
    code = (ura_code or "").strip()
    if code in ("18", "0.18"):
        return 18.0, "01", "Standard", "0.18"
    elif code == "00":
        return 0.0,  "11", "F: VAT OUT OF SCOPE", "0"
    elif code == "0":
        return 0.0,  "02", "Zero-rated", "0"
    elif code == "-":
        return 0.0,  "03", "Exempt", "-"
    else:
        # Unknown / empty — default to standard 18% (SAP B1 fallback)
        return 18.0, "01", "Standard", "0.18"


def _tax_rate_label(ura_rate_str: str, category_code: str = "") -> str:
    """Human-readable label for the URA taxRateName field."""
    if category_code == "04":
        return "Deemed Vat Invoice"
    if category_code == "11":
        return "0%"
    if ura_rate_str == "0.18":
        return "VAT 18%"
    elif ura_rate_str == "0":
        return "VAT 0%"
    elif ura_rate_str == "-":
        return "Exempt"
    return f"VAT {ura_rate_str}"


def _save_result(doc, result: dict):
    """
    Persist URA T109/T106 response to the Sales Invoice EFRIS tab fields.

    SAP B1 field mapping (ARReserveInvoice.cs → SaveEFRISResult):
      response.BasicInformation.InvoiceId    → efris_invoice_id
      response.BasicInformation.AntifakeCode → efris_antifake_code
      response.BasicInformation.invoiceNo    → efris_fdn_number
      QR PNG image (generated locally)       → efris_qr_code   (base64 PNG for display)
      URA validation URL                     → efris_qr_path   (clickable link)
      remark / returnMessage                 → efris_remark
    """
    # ── 0. Guard: empty response ──────────────────────────────────────────────
    if not result:
        frappe.log_error(
            f"_save_result called with empty result for {doc.name}. "
            "URA returned returnCode=00 but no response body. "
            "Check EFRIS Log for the raw envelope — the invoice may still be registered.",
            f"EFRIS Empty T109 Response [{doc.name}]",
        )
        # Do NOT set any fields — caller will attempt T106 fallback
        return

    # ── 1. Unwrap list-wrapped responses ─────────────────────────────────────
    # URA occasionally returns the body as a JSON array.
    # _parse_plain_content wraps that as {"data": [...]}.
    # Unwrap it here so the rest of the function always sees a plain dict.
    if isinstance(result, dict) and not result.get("basicInformation"):
        data_list = result.get("data")
        if isinstance(data_list, list) and data_list:
            first = data_list[0]
            if isinstance(first, dict):
                result = first

    # ── 2. Read nested sub-objects (SAP B1: response.BasicInformation / .Summary) ─
    basic_info   = result.get("basicInformation") or {}
    summary_resp = result.get("summary") or {}

    frappe.logger("efris").info(
        f"[_save_result] {doc.name}: "
        f"basicInfo keys={list(basic_info.keys())} | "
        f"summary keys={list(summary_resp.keys())} | "
        f"top-level keys={list(result.keys())}"
    )

    invoice_id = basic_info.get("invoiceId", "")    or result.get("invoiceId", "")
    antifake   = basic_info.get("antifakeCode", "") or result.get("antifakeCode", "")
    fdn_number = (
        basic_info.get("invoiceNo", "")
        or result.get("invoiceNo", "")
        or result.get("fdnNo", "")
    )
    remark = (
        result.get("remark", "")
        or result.get("returnMessage", "")
        or summary_resp.get("remarks", "")
        or ("SUCCESS" if invoice_id else "Registered — awaiting URA details")
    )

    # ── 3. Build the URA validation URL ──────────────────────────────────────
    # Correct URL format (confirmed from URA portal):
    #   sandbox:    https://efristest.ura.go.ug/site_new/#/invoiceValidation?invoiceNo=...&antiFakeCode=...
    #   production: https://efris.ura.go.ug/site_new/#/invoiceValidation?invoiceNo=...&antiFakeCode=...
    qr_raw = summary_resp.get("qrCode", "") or result.get("qrCode", "")

    validation_url = ""
    if antifake and fdn_number:
        validation_url = ura_validation_url(doc.company, fdn_number, antifake)
    elif qr_raw:
        validation_url = qr_raw
    elif antifake:
        validation_url = antifake

    # ── 4. Persist to EFRIS tab fields ───────────────────────────────────────
    # efris_qr_code  → the validation URL (human-readable; QR image generated on demand)
    # efris_qr_path  → same URL (used by "Verify on URA" button and QR dialog)
    # The "View QR Code" button in efris_utils.js calls get_qr_code_image() server-side
    # to render a PNG on-the-fly from the URL — no need to persist the base64 blob.
    # Fiscal date = when URA issued the fiscal document (SAP U_CVT_EFRISDATE)
    issued = str(basic_info.get("issuedDate") or "").strip()
    if issued:
        doc.db_set("efris_fiscal_date", issued, update_modified=False)
    if invoice_id:
        doc.db_set("efris_invoice_id",    invoice_id,     update_modified=False)
    if antifake:
        doc.db_set("efris_antifake_code", antifake,       update_modified=False)
    if fdn_number:
        doc.db_set("efris_fdn_number",    fdn_number,     update_modified=False)
    if validation_url:
        # Both fields get the URL — efris_qr_code shows it on the form,
        # efris_qr_path drives the "Verify on URA" button
        doc.db_set("efris_qr_code",  validation_url, update_modified=False)
        doc.db_set("efris_qr_path",  validation_url, update_modified=False)
    doc.db_set("efris_remark",        remark,         update_modified=False)
    # Only mark "Submitted" when we have a real URA invoice_id — not just returnCode=00
    efris_status = "Submitted" if invoice_id else "Pending"
    doc.db_set("efris_status",        efris_status,   update_modified=False)
    doc.db_set("efris_submitted_at",  datetime.now(), update_modified=False)


def ura_validation_url(company: str, fdn: str, antifake: str) -> str:
    """URA portal page that verifies a fiscal document (also what the QR code encodes)."""
    api_url = getattr(get_efris_settings(company), "api_url", "") or ""
    host = "https://efristest.ura.go.ug" if "efristest" in api_url.lower() else "https://efris.ura.go.ug"
    return f"{host}/site_new/#/invoiceValidation?invoiceNo={fdn}&antiFakeCode={antifake}"


@frappe.whitelist()
def get_qr_code_image(validation_url: str) -> str:
    """
    Generate a QR code PNG and return as a base64 data-URI.
    Called by the "View QR Code" button in efris_utils.js.
    efris_qr_code stores the URL; this converts it to a scannable image on demand.
    """
    if not validation_url:
        return ""
    return generate_qr_code_b64(validation_url)
