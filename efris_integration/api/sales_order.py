"""
T109 — Submit Sales Order / Delivery Note to EFRIS

Pricing modes:
  Sales Order  → gross_on_sales_order  /  price_on_sales_order
  Delivery Note → gross_on_delivery    /  price_on_delivery

Mirrors: SalesOrder.cs, Delivery.cs, UraSalesOrder.cs, T109.cs
"""

from datetime import datetime

import frappe

from efris_integration.api.base import EFRISClient
from efris_integration.api.invoice import (
    _basic_info,
    _build_goods_and_tax,
    _buyer_details,
    _pay_way,
    _seller_details,
    _save_result,
)
from efris_integration.utils.helpers import (
    get_customer_efris_fields,
    get_efris_settings,
    resolve_branch_for_doc,
)


# ─── Sales Order → T109 ───────────────────────────────────────────────────

@frappe.whitelist()
def submit_sales_order(order_name: str) -> dict:
    doc = frappe.get_doc("Sales Order", order_name)
    if doc.docstatus != 1:
        frappe.throw("Sales Order must be submitted first.")
    if doc.get("efris_invoice_id"):
        frappe.throw(f"Already submitted to EFRIS: {doc.efris_invoice_id}")

    settings = get_efris_settings(doc.company)
    return _post_t109(
        doc, settings,
        "Sales Order", order_name,
        use_gross=bool(settings.gross_on_sales_order),
        use_price=bool(settings.price_on_sales_order),
    )


# ─── Delivery Note → T109 ─────────────────────────────────────────────────

@frappe.whitelist()
def submit_delivery_note(delivery_name: str) -> dict:
    doc = frappe.get_doc("Delivery Note", delivery_name)
    if doc.docstatus != 1:
        frappe.throw("Delivery Note must be submitted first.")
    if doc.get("efris_invoice_id"):
        frappe.throw(f"Already submitted to EFRIS: {doc.efris_invoice_id}")

    settings = get_efris_settings(doc.company)
    return _post_t109(
        doc, settings,
        "Delivery Note", delivery_name,
        use_gross=bool(settings.gross_on_delivery),
        use_price=bool(settings.price_on_delivery),
    )


# ─── Internal ────────────────────────────────────────────────────────────

def _post_t109(
    doc,
    settings,
    doctype: str,
    docname: str,
    use_gross: bool,
    use_price: bool,
) -> dict:
    resolve_branch_for_doc(doc, settings)   # multi-branch: resolve from warehouse
    client = EFRISClient(company=doc.company)
    client.settings.branch_id   = settings.branch_id
    client.settings.branch_name = settings.branch_name
    buyer  = get_customer_efris_fields(doc.customer)
    goods_details, tax_details, summary = _build_goods_and_tax(
        doc, settings,
        use_gross=use_gross,
        use_price=use_price,
        company=doc.company,
    )

    # If Delivery Note, link to the source Sales Order's EFRIS Invoice ID
    ori_invoice_id = ""
    if doctype == "Delivery Note":
        so_name = _get_source_so(doc)
        if so_name:
            ori_invoice_id = (
                frappe.db.get_value("Sales Order", so_name, "efris_invoice_id") or ""
            )

    payload = {
        "sellerDetails":    _seller_details(settings, doc),
        "buyerDetails":     _buyer_details(doc, buyer),
        "basicInformation": _basic_info(
            doc, settings, invoice_type="1", ori_invoice_id=ori_invoice_id
        ),
        "goodsDetails":     goods_details,
        "taxDetails":       tax_details,
        "summary":          summary,
        "payWay":           _pay_way(doc),
        "extend":           {"reason": "", "reasonCode": ""},
    }

    result = client.call("T109", payload, encrypt=True, sign=True, doc_type=doctype, doc_name=docname)
    _save_result(doc, result)

    frappe.msgprint(
        f"✅ {doctype} {docname} submitted to EFRIS.\nID: {result.get('invoiceId', '')}",
        indicator="green",
    )
    return result


def _get_source_so(delivery_note) -> str:
    for item in delivery_note.items:
        so = item.get("against_sales_order")
        if so:
            return so
    return ""
