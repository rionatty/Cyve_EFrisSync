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
from efris_integration.utils.helpers import (
    generate_qr_code_b64,
    get_customer_efris_fields,
    get_efris_settings,
    get_item_efris_fields,
    map_tax_code,
    map_uom,
    safe_float,
    safe_str,
)
from efris_integration.excise import compute_excise_for_line


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
    client   = EFRISClient(company=doc.company)

    # T127 prefetch if enabled
    if settings.use_t127:
        from efris_integration.api.t127 import get_industry_config
        try:
            get_industry_config(company=doc.company)
        except Exception as e:
            frappe.log_error(str(e), "EFRIS T127 prefetch failed — continuing without")

    buyer = get_customer_efris_fields(doc.customer)
    goods_details, tax_details, summary = _build_goods_and_tax(
        doc, settings,
        company=doc.company,
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

    result = client.call("T109", payload, encrypt=True, sign=True,
                         doc_type="Sales Invoice", doc_name=invoice_name)

    # ── Log raw result so we can debug response structure ─────────────────
    frappe.logger("efris").info(
        f"[T109 raw result] {invoice_name}: "
        f"keys={list(result.keys()) if result else 'EMPTY_DICT'} | "
        f"preview={frappe.as_json(result)[:400]}"
    )

    _save_result(doc, result)

    # ── T106 fallback: if T109 returned no invoice details, query URA ─────
    # URA sometimes returns returnCode=00 but an empty response body.
    # The invoice IS registered — T106 is the reliable way to confirm and
    # retrieve invoiceId, antifakeCode, FDN number, and QR code.
    doc.reload()
    if not doc.get("efris_invoice_id"):
        frappe.logger("efris").info(
            f"[T109] No invoice_id in response for {invoice_name} — "
            "attempting T106 fallback to fetch URA invoice details"
        )
        try:
            _t106_fallback(doc, client, invoice_name)
            doc.reload()
        except Exception as exc:
            frappe.log_error(
                frappe.get_traceback(),
                f"EFRIS T106 fallback failed [{invoice_name}]"
            )

    # ── Success toast ──────────────────────────────────────────────────────
    doc.reload()
    _inv_id = doc.get("efris_invoice_id") or ""
    frappe.msgprint(
        f"✅ Invoice {invoice_name} submitted to EFRIS."
        + (f"<br>Invoice ID: <b>{_inv_id}</b>" if _inv_id else
           "<br><b>Note:</b> URA confirmed receipt but did not return invoice details yet. "
           "Click <b>Refresh from EFRIS</b> in the EFRIS menu to fetch them."),
        indicator="green",
        title="EFRIS Submission",
    )
    return result


# ─── Fetch & save URA details for an already-submitted invoice ───────────────

@frappe.whitelist()
def fetch_invoice_from_ura(invoice_name: str) -> dict:
    """
    Refresh EFRIS tab fields by querying URA T106 for this invoice.
    Called by the "Refresh from EFRIS" button on the Sales Invoice form.
    Works even when efris_invoice_id is empty (T109 returned no details).
    """
    doc    = frappe.get_doc("Sales Invoice", invoice_name)
    client = EFRISClient(company=doc.company)
    _t106_fallback(doc, client, invoice_name)
    doc.reload()
    inv_id = doc.get("efris_invoice_id") or ""
    if inv_id:
        return {"status": "ok", "invoice_id": inv_id}
    return {"status": "not_found",
            "message": "Invoice not yet visible in URA T106. Try again in a few moments."}


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


# ─── T106 fallback: called after T109 returns empty content ───────────────────

def _t106_fallback(doc, client: "EFRISClient", invoice_name: str) -> None:
    """
    Query T106 to retrieve invoice details when T109 returned empty content.

    URA EFRIS sometimes returns returnCode="00" on T109 but an empty response
    body — the invoice IS registered on URA's side, we just don't get the
    invoiceId / antifakeCode / FDN back in the T109 response.

    This function queries T106 (list invoices), identifies our invoice by
    sellerDetails.referenceNo or basicInformation.invoiceNo, then calls
    _save_result() with the matched record so the EFRIS tab fields are populated.
    """
    from datetime import date

    today = date.today().strftime("%Y-%m-%d")

    # First try: search by our ERPNext invoice name (sent as referenceNo in T109)
    # Some EFRIS versions support filtering by invoiceNo (which we set to doc.name)
    for search_no in [invoice_name, ""]:
        payload = {
            "invoiceNo": search_no,
            "startDate": today,
            "endDate":   today,
            "pageNo":    "1",
            "pageSize":  "20",
        }
        try:
            result = client.call("T106", payload, encrypt=True, sign=True,
                                 doc_type="Sales Invoice", doc_name=invoice_name)
        except Exception as exc:
            frappe.log_error(str(exc), f"EFRIS T106 fallback query failed [{invoice_name}]")
            return

        frappe.logger("efris").info(
            f"[T106 fallback] {invoice_name}: search_no={search_no!r} "
            f"keys={list(result.keys()) if result else 'EMPTY'} "
            f"preview={frappe.as_json(result)[:300]}"
        )

        if not result:
            continue

        # ── Normalise T106 response to a flat list of invoice records ──────
        invoices = _extract_invoice_list(result)

        if not invoices:
            frappe.log_error(
                f"T106 returned no invoice records for {invoice_name}. "
                f"Result: {frappe.as_json(result)[:500]}",
                f"EFRIS T106 Fallback — empty list [{invoice_name}]",
            )
            continue

        # ── Find the matching invoice ──────────────────────────────────────
        matched = None
        for inv in invoices:
            if not isinstance(inv, dict):
                continue
            basic  = inv.get("basicInformation") or {}
            seller = inv.get("sellerDetails")    or {}
            # Match by our ERPNext name sent as referenceNo OR as basicInformation.invoiceNo
            if (seller.get("referenceNo") == invoice_name
                    or basic.get("invoiceNo") == invoice_name):
                matched = inv
                break

        # Fallback: take the first record returned (most recent upload today)
        if not matched:
            matched = invoices[0] if invoices else None

        if matched:
            frappe.logger("efris").info(
                f"[T106 fallback] Matched invoice for {invoice_name}: "
                f"basicInfo={list((matched.get('basicInformation') or {}).keys())}"
            )
            _save_result(doc, matched)
            return

    frappe.log_error(
        f"T106 fallback exhausted all attempts for {invoice_name}. "
        "Invoice may not yet be visible in T106. "
        "Use 'Refresh from EFRIS' later to fetch URA details.",
        f"EFRIS T106 Fallback — not found [{invoice_name}]",
    )


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


# ─── Shared payload builders (also used by credit_note.py / sales_order.py) ──

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
    branch_id = (
        doc.get("efris_branch_id")
        or settings.branch_id
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
        "vatApplicableFlag":   "0",
        "isBatch":             "0",            # T109 API spec: "0" = not a batch invoice
    }


def _build_goods_and_tax(
    doc,
    settings,
    company: str = "",
) -> tuple[list, list, dict]:
    """
    Build goodsDetails, taxDetails, and summary for any invoice-like doc.

    Mirrors SAP B1 CreateInvoice() exactly:
      • unitPrice / total    → always VAT-INCLUSIVE (gross)
      • taxRate              → URA decimal string: "0.18", "0", "-"
      • taxCategoryCode      → "01" (standard), "02" (zero), "03" (exempt)
      • modeCode             → "1"
      • discountFlag="2"     → no discountTotal / discountTaxRate keys sent
      • remainAmount         → equals total when no discount
    """
    goods_details: list[dict] = []
    # tax buckets keyed by URA category code ("01", "02", "03")
    tax_buckets: dict[str, dict] = {}
    total_net   = 0.0
    total_tax   = 0.0
    total_gross = 0.0

    for idx, line in enumerate(doc.items, start=1):
        _company    = company or doc.company or ""
        item_fields = get_item_efris_fields(line.item_code)
        uom_code    = map_uom(line.uom, _company)

        # Resolve URA tax info + ERPNext-computed per-line tax amount
        tax_rate, cat_code, cat_name, ura_rate_str, erp_tax_amount = \
            _resolve_line_tax(doc, line, _company)

        # ── Amounts ─────────────────────────────────────────────────────────
        net_unit_price  = safe_float(line.rate)
        qty             = safe_float(line.qty)
        net_amount      = safe_float(
            getattr(line, "net_amount", None) or line.amount
        )

        # Gross unit price: VAT-inclusive, kept at 4 dp for precision
        gross_unit_price = round(net_unit_price * (1 + tax_rate / 100), 4)

        # URA rule [1316]: total = trunc(qty × unitPrice, 2)  — TRUNCATE, not round
        gross_amount = _trunc2(qty * gross_unit_price)

        # URA rule [2776]: tax = trunc(total × taxRate / (1 + taxRate), 2)
        # total is VAT-inclusive (gross), so tax is back-extracted from gross.
        # e.g. gross=118, rate=18% → tax = trunc(118 × 0.18/1.18, 2) = 18.00
        # Using gross-minus-net would differ by 0.01 due to truncation gaps.
        if tax_rate > 0:
            tax_decimal = tax_rate / 100           # 18.0 → 0.18
            tax_amount  = _trunc2(gross_amount * tax_decimal / (1 + tax_decimal))
        else:
            tax_amount  = 0.0

        # Derive net from gross so buckets stay self-consistent:
        #   net = gross - tax  (both already trunc'd to 2dp)
        # Using ERPNext net_amount for bucket accumulation causes net+tax ≠ gross
        # across multi-line invoices due to independent truncation (URA error 1342).
        line_net     = round(gross_amount - tax_amount, 2)
        total_net   += line_net
        total_tax   += tax_amount
        total_gross += gross_amount

        # ── Excise (T127 rates if enabled) ──────────────────────────────────
        excise_data = compute_excise_for_line(
            line, item_fields, settings, doc.currency
        )

        # ── Discount ────────────────────────────────────────────────────────
        # SAP B1: discountFlag="2" → discountTotal / discountTaxRate omitted
        # discountFlag="1" → item carries discount; must be followed by flag="0" line
        # For simplicity we use flag="2" (no explicit discount line) — the discount
        # is already reflected in the net unit price ERPNext stores after applying it.
        discount_flag = "2"

        # ── Goods item ──────────────────────────────────────────────────────
        goods_cat_id = item_fields.get("efris_goods_category_id") or "238"

        # vatApplicableFlag: always "1" for VAT-registered companies.
        # "0" = "Out of Scope for VAT" — requires special URA authorization (error 2856).
        # The actual rate (18%, 0%, exempt) is carried by taxRate + taxCategoryCode.
        vat_applicable = "1"

        good: dict = {
            "item":              line.item_name or line.item_code,
            "itemCode":          line.item_code,
            "qty":               str(qty),
            "unitOfMeasure":     uom_code,
            # SAP B1: UnitPrice is always VAT-inclusive (gross)
            "unitPrice":         str(gross_unit_price),
            "total":             str(gross_amount),
            "taxRate":           ura_rate_str,        # "0.18", "0", or "-"
            "tax":               str(tax_amount),
            # remainAmount = total when no discount (SAP B1 requirement)
            "remainAmount":      str(gross_amount),
            "orderNumber":       str(idx - 1),         # URA: must start from 0 (error 1219)
            "discountFlag":      discount_flag,        # "2" = no discount
            "deemedFlag":        "2",                  # "2" = not a deemed supply
            "vatApplicableFlag": vat_applicable,       # T109 API spec field
            "categoryId":        goods_cat_id,
            "categoryName":      line.item_group or "",
            "goodsCategoryId":   goods_cat_id,
            "goodsCategoryName": line.item_group or "",
            **excise_data,
        }
        goods_details.append(good)

        # ── Aggregate tax buckets ────────────────────────────────────────────
        if cat_code not in tax_buckets:
            tax_buckets[cat_code] = {
                "net": 0.0, "tax": 0.0, "gross": 0.0,
                "rate": ura_rate_str, "name": cat_name,
            }
        tax_buckets[cat_code]["net"]   += line_net      # gross - tax (self-consistent)
        tax_buckets[cat_code]["tax"]   += tax_amount
        tax_buckets[cat_code]["gross"] += gross_amount

    # ── Tax details ─────────────────────────────────────────────────────────
    # URA rule [1342]: netAmount + taxAmount MUST equal grossAmount exactly.
    # We derive grossAmount = net + tax (never sum independently) so it holds
    # even when individual line truncations produce sub-cent gaps.
    tax_details = []
    for code, v in tax_buckets.items():
        t_net   = round(v["net"],  2)
        t_tax   = round(v["tax"],  2)
        t_gross = round(t_net + t_tax, 2)   # derived — guarantees constraint
        tax_details.append({
            "taxCategory":     v["name"],
            "taxCategoryCode": code,
            "netAmount":       str(t_net),
            "taxRate":         v["rate"],
            "taxAmount":       str(t_tax),
            "grossAmount":     str(t_gross),  # = netAmount + taxAmount  ✓
            "exciseUnit":      "",
            "exciseCurrency":  "",
            "taxRateName":     _tax_rate_label(v["rate"]),
        })

    # ── Summary ─────────────────────────────────────────────────────────────
    # Same consistency rule: grossAmount = netAmount + taxAmount.
    s_net   = round(total_net,  2)
    s_tax   = round(total_tax,  2)
    s_gross = round(s_net + s_tax, 2)   # derived — never independently summed
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
    for tax in (doc.taxes or []):
        if tax.charge_type != "On Net Total":
            continue

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
      ""    → fallback to 18%   → "01", "0.18"
    """
    code = (ura_code or "").strip()
    if code in ("18", "0.18"):
        return 18.0, "01", "Standard", "0.18"
    elif code == "0":
        return 0.0,  "02", "Zero-rated", "0"
    elif code == "-":
        return 0.0,  "03", "Exempt", "-"
    else:
        # Unknown / empty — default to standard 18% (SAP B1 fallback)
        return 18.0, "01", "Standard", "0.18"


def _tax_rate_label(ura_rate_str: str) -> str:
    """Human-readable label for the URA taxRateName field."""
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
    # URA returns qrCode inside summary; use it if present, otherwise construct
    # the standard verification URL from antifakeCode + invoiceNo (FDN number).
    # This mirrors how SAP B1 builds the verification link shown to the user.
    qr_raw = summary_resp.get("qrCode", "") or result.get("qrCode", "")

    validation_url = ""
    if antifake and fdn_number:
        settings   = get_efris_settings(doc.company)
        api_url    = getattr(settings, "api_url", "") or ""
        sandbox    = "efristest" in api_url.lower()
        efris_host = "https://efristest.ura.go.ug" if sandbox else "https://efris.ura.go.ug"
        validation_url = (
            f"{efris_host}/efrisinvoice/#/invoiceValidation"
            f"?invoiceNo={fdn_number}&antiFakeCode={antifake}"
        )
    elif qr_raw:
        validation_url = qr_raw
    elif antifake:
        validation_url = antifake

    # ── 4. Generate QR code PNG image (base64) ───────────────────────────────
    # SAP B1 saves a PNG locally via localPath; we store it as a base64 data-URI
    # so the "View QR Code" button can render it as <img> without any file I/O.
    # Encode the validation URL — scanning it opens the URA invoice viewer.
    qr_image_b64 = ""
    encode_target = validation_url or qr_raw or antifake
    if encode_target:
        try:
            qr_image_b64 = generate_qr_code_b64(encode_target)
        except Exception as exc:
            frappe.log_error(str(exc), f"EFRIS QR image generation failed [{doc.name}]")

    # ── 5. Persist to EFRIS tab fields ───────────────────────────────────────
    # Only update fields that we actually have data for
    if invoice_id:
        doc.db_set("efris_invoice_id",    invoice_id,    update_modified=False)
    if antifake:
        doc.db_set("efris_antifake_code", antifake,      update_modified=False)
    if fdn_number:
        doc.db_set("efris_fdn_number",    fdn_number,    update_modified=False)
    # efris_qr_code  → base64 PNG data-URI  (rendered as <img> in View QR dialog)
    if qr_image_b64:
        doc.db_set("efris_qr_code",       qr_image_b64,  update_modified=False)
    # efris_qr_path  → URA verification URL (clickable link for manual validation)
    if validation_url:
        doc.db_set("efris_qr_path",       validation_url, update_modified=False)
    doc.db_set("efris_remark",        remark,         update_modified=False)
    # Only mark "Submitted" when we have a real URA invoice_id — not just returnCode=00
    efris_status = "Submitted" if invoice_id else "Pending"
    doc.db_set("efris_status",        efris_status,   update_modified=False)
    doc.db_set("efris_submitted_at",  datetime.now(), update_modified=False)


@frappe.whitelist()
def get_qr_code_image(validation_url: str) -> str:
    """
    Generate a QR code PNG and return as a base64 data-URI.
    Called by the View QR Code button when efris_qr_code is empty
    (e.g. for invoices submitted before the QR generation fix).
    """
    if not validation_url:
        return ""
    return generate_qr_code_b64(validation_url)
