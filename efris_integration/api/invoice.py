"""
T108 — Submit AR Invoice (Sales Invoice) to EFRIS
T106 — Query invoices

Mirrors: ARInvoice.cs, UraARInvoice.cs, T108.cs, T106.cs

Pricing modes (controlled by EFRIS Settings):
  gross_on_ar_invoice  → unitPrice and total are VAT-inclusive (gross)
  price_on_ar_invoice  → include unitPrice field; otherwise send "0"
  use_t127             → fetch live excise rates from URA T127 before submission
"""

from datetime import datetime

import frappe

from efris_integration.api.base import EFRISClient, EFRISAPIError
from efris_integration.utils.helpers import (
    generate_qr_code_b64,
    get_customer_efris_fields,
    get_item_efris_fields,
    map_tax_code,
    map_uom,
    safe_float,
    safe_str,
)
from efris_integration.excise import compute_excise_for_line


# ─── T108: Submit Sales Invoice ───────────────────────────────────────────

@frappe.whitelist()
def submit_sales_invoice(invoice_name: str) -> dict:
    doc = frappe.get_doc("Sales Invoice", invoice_name)

    if doc.docstatus != 1:
        frappe.throw("Invoice must be submitted before sending to EFRIS.")
    if doc.get("efris_invoice_id"):
        frappe.throw(f"Invoice already submitted to EFRIS (ID: {doc.efris_invoice_id}).")

    settings = frappe.get_single("EFRIS Settings")
    client = EFRISClient()

    # T127 prefetch if enabled
    if settings.use_t127:
        from efris_integration.api.t127 import get_industry_config
        try:
            get_industry_config()
        except Exception as e:
            frappe.log_error(str(e), "EFRIS T127 prefetch failed — continuing without")

    buyer = get_customer_efris_fields(doc.customer)
    goods_details, tax_details, summary = _build_goods_and_tax(
        doc, settings,
        use_gross=bool(settings.gross_on_ar_invoice),
        use_price=bool(settings.price_on_ar_invoice),
    )

    payload = {
        "sellerDetails":  _seller_details(settings, doc),
        "buyerDetails":   _buyer_details(doc, buyer),
        "basicInformation": _basic_info(doc, settings, invoice_type="1"),
        "goodsDetails":   goods_details,
        "taxDetails":     tax_details,
        "summary":        summary,
        "payWay":         _pay_way(doc),
        "extend":         {"reason": "", "reasonCode": ""},
    }

    result = client.call("T108", payload, doc_type="Sales Invoice", doc_name=invoice_name)

    _save_result(doc, result)
    frappe.msgprint(
        f"✅ Invoice {invoice_name} submitted to EFRIS.\nInvoice ID: {result.get('invoiceId', '')}",
        indicator="green",
    )
    return result


# ─── T106: Query Invoices ─────────────────────────────────────────────────

@frappe.whitelist()
def query_invoices(
    invoice_no: str = "",
    from_date: str = "",
    to_date: str = "",
    page: int = 1,
) -> dict:
    client = EFRISClient()
    payload = {
        "invoiceNo": invoice_no,
        "startDate": from_date,
        "endDate": to_date,
        "pageNo": str(page),
        "pageSize": "20",
    }
    return client.call("T106", payload, doc_type="Sales Invoice", doc_name="Query")


# ─── Shared payload builders (also used by credit_note.py / sales_order.py) ──

def _seller_details(settings, doc) -> dict:
    branch_id = (
        doc.get("efris_branch_id")
        or settings.branch_id
        or ""
    )
    return {
        "tin":             settings.tin,
        "ninBrn":          settings.taxpayer_id,
        "passportNumber":  "",
        "legalName":       settings.legal_name or "",
        "businessName":    settings.company_name or "",
        "address":         settings.address or "",
        "mobilePhone":     settings.phone or "",
        "linePhone":       settings.phone or "",
        "emailAddress":    settings.email or "",
        "placeOfBusiness": settings.address or "",
        "referenceNo":     doc.name,
        "branchId":        branch_id,
        "branchName":      settings.branch_name or "",
        "branchCode":      settings.branch_code or "",
    }


def _buyer_details(doc, buyer: dict) -> dict:
    buyer_type = buyer.get("efris_buyer_type") or "B2C"
    return {
        "buyerTin":         safe_str(buyer.get("tax_id")),
        "buyerNinBrn":      safe_str(buyer.get("efris_nin_brn")),
        "buyerPassportNum": "",
        "buyerLegalName":   doc.customer_name or "",
        "buyerBusinessName":doc.customer_name or "",
        "buyerAddress":     doc.address_display or "",
        "buyerEmail":       safe_str(buyer.get("email_id")),
        "buyerMobilePhone": safe_str(buyer.get("mobile_no")),
        "buyerLinePhone":   safe_str(buyer.get("mobile_no")),
        "buyerPlaceOfBusi": doc.address_display or "",
        "buyerType":        buyer_type,
        "buyerCitizenship": safe_str(buyer.get("efris_citizenship")),
        "buyerSector":      safe_str(buyer.get("efris_sector")),
        "buyerReferenceNo": doc.get("po_no") or "",
    }


def _basic_info(
    doc,
    settings,
    invoice_type: str = "1",
    ori_invoice_id: str = "",
) -> dict:
    posting = doc.posting_date
    if hasattr(posting, "strftime"):
        issued_date = posting.strftime("%Y-%m-%d %H:%M:%S")
    else:
        issued_date = str(posting) + " 00:00:00"

    return {
        "invoiceId":           "",
        "invoiceNo":           doc.name,
        "antifakeCode":        "",
        "deviceNo":            settings.device_no,
        "issuedDate":          issued_date,
        "operator":            frappe.session.user,
        "currency":            doc.currency or "UGX",
        "oriInvoiceId":        ori_invoice_id,
        "invoiceType":         invoice_type,   # 1=normal, 2=credit note
        "invoiceKind":         "1",            # 1=normal, 2=prepayment
        "dataSource":          "103",          # 103=ERP system
        "invoiceIndustryCode": settings.industry_code or "101",
        "vatApplicableFlag":   "0",
    }


def _build_goods_and_tax(
    doc,
    settings,
    use_gross: bool = False,
    use_price: bool = True,
) -> tuple[list, list, dict]:
    """
    Build goodsDetails, taxDetails, and summary arrays for any invoice-like doc.

    use_gross=True  → unitPrice and total are VAT-inclusive (gross)
    use_price=False → unitPrice field sent as "0" (omit pricing)
    """
    goods_details: list[dict] = []
    tax_totals: dict[str, dict] = {}
    total_net   = 0.0
    total_tax   = 0.0
    total_gross = 0.0

    for idx, line in enumerate(doc.items, start=1):
        item_fields = get_item_efris_fields(line.item_code)
        tax_rate    = _get_line_tax_rate(doc, line)
        uom_code    = map_uom(line.uom)
        tax_cat_code = map_tax_code(_get_line_tax_account(doc, line))

        # Base amounts (net)
        net_unit_price = safe_float(line.rate)
        qty            = safe_float(line.qty)
        net_amount     = safe_float(
            getattr(line, "net_amount", None) or line.amount
        )
        tax_amount     = round(net_amount * tax_rate / 100, 2)
        gross_amount   = round(net_amount + tax_amount, 2)
        gross_unit_price = round(net_unit_price * (1 + tax_rate / 100), 4)

        total_net   += net_amount
        total_tax   += tax_amount
        total_gross += gross_amount

        # Excise calculation — uses T127 rates if enabled
        excise_data = compute_excise_for_line(
            line, item_fields, settings, doc.currency
        )

        # Apply Gross / Price settings
        reported_unit_price = gross_unit_price if use_gross else net_unit_price
        reported_total      = gross_amount if use_gross else net_amount

        good: dict = {
            "item":              line.item_name or line.item_code,
            "itemCode":          line.item_code,
            "qty":               str(qty),
            "unitOfMeasure":     uom_code,
            "unitPrice":         str(round(reported_unit_price, 4)) if use_price else "0",
            "total":             str(round(reported_total, 2)),
            "taxRate":           str(tax_rate),
            "tax":               str(tax_amount),
            "discountTotal":     str(safe_float(getattr(line, "discount_amount", 0))),
            "discountTaxRate":   "0",
            "orderNumber":       str(idx),
            "discountFlag":      "2",
            "deemedFlag":        "2",
            "categoryId":        item_fields.get("efris_goods_category_id") or "238",
            "categoryName":      line.item_group or "",
            "goodsCategoryId":   item_fields.get("efris_goods_category_id") or "238",
            "goodsCategoryName": line.item_group or "",
            **excise_data,
        }
        goods_details.append(good)

        # Aggregate per tax category
        if tax_cat_code not in tax_totals:
            tax_totals[tax_cat_code] = {
                "net": 0.0, "tax": 0.0, "gross": 0.0, "rate": tax_rate
            }
        tax_totals[tax_cat_code]["net"]   += net_amount
        tax_totals[tax_cat_code]["tax"]   += tax_amount
        tax_totals[tax_cat_code]["gross"] += gross_amount

    tax_details = [
        {
            "taxCategory":     _tax_category_name(code),
            "taxCategoryCode": code,
            "netAmount":       str(round(v["net"],   2)),
            "taxRate":         str(v["rate"]),
            "taxAmount":       str(round(v["tax"],   2)),
            "grossAmount":     str(round(v["gross"], 2)),
            "exciseUnit":      "",
            "exciseCurrency":  "",
            "taxRateName":     f"VAT {v['rate']}%",
        }
        for code, v in tax_totals.items()
    ]

    summary = {
        "netAmount":   str(round(total_net,   2)),
        "taxAmount":   str(round(total_tax,   2)),
        "grossAmount": str(round(total_gross, 2)),
        "itemCount":   str(len(goods_details)),
        "modeCode":    "0",
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


def _get_line_tax_rate(doc, line) -> float:
    for tax in (doc.taxes or []):
        if tax.charge_type == "On Net Total":
            return safe_float(tax.rate)
    return 18.0


def _get_line_tax_account(doc, line) -> str:
    for tax in (doc.taxes or []):
        return tax.account_head or ""
    return ""


def _tax_category_name(code: str) -> str:
    return {"101": "Standard Rate", "102": "Zero Rate", "103": "Exempt"}.get(
        code, "Standard Rate"
    )


def _save_result(doc, result: dict):
    invoice_id   = result.get("invoiceId", "")
    antifake     = result.get("antifakeCode", "")
    qr_code      = result.get("qrCode", "")
    if not qr_code and antifake:
        qr_code = generate_qr_code_b64(antifake)

    doc.db_set("efris_invoice_id",    invoice_id,       update_modified=False)
    doc.db_set("efris_antifake_code", antifake,         update_modified=False)
    doc.db_set("efris_qr_code",       qr_code,          update_modified=False)
    doc.db_set("efris_status",        "Submitted",      update_modified=False)
    doc.db_set("efris_submitted_at",  datetime.now(),   update_modified=False)
