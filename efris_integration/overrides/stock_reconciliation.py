"""
Stock Reconciliation hook — equivalent to SAP B1 Inventory Posting.
Submits to EFRIS T131 with stock-in option 104 (Opening Stock).
"""

import frappe
from efris_integration.utils.helpers import get_efris_settings


def on_submit(doc, method):
    try:
        get_efris_settings(doc.company)
    except Exception:
        return  # EFRIS not configured for this company — skip silently
    try:
        _submit_opening_stock(doc)
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), f"EFRIS Stock Reconciliation Failed: {doc.name}")
        frappe.msgprint(f"EFRIS opening stock submission failed: {e}.", indicator="orange", alert=True)


@frappe.whitelist()
def _submit_opening_stock(doc):
    from efris_integration.api.base import EFRISClient
    from efris_integration.utils.helpers import (
        get_efris_settings,
        get_item_efris_fields,
        map_uom,
        safe_float,
    )

    if isinstance(doc, str):
        doc = frappe.get_doc("Stock Reconciliation", doc)

    settings = get_efris_settings(doc.company)
    client   = EFRISClient(company=doc.company)

    goods_details = []
    total = 0.0
    for idx, line in enumerate(doc.items, start=1):
        item_fields = get_item_efris_fields(line.item_code)
        amount = safe_float(
            line.amount or (safe_float(line.qty) * safe_float(line.valuation_rate))
        )
        total += amount
        goods_details.append(
            {
                "item":          line.item_name or line.item_code,
                "itemCode":      line.item_code,
                "qty":           str(safe_float(line.qty)),
                "unitOfMeasure": map_uom(line.stock_uom, doc.company),
                "unitPrice":     str(safe_float(line.valuation_rate)),
                "total":         str(amount),
                "orderNumber":   str(idx),
                "categoryId":    item_fields.get("efris_goods_category_id") or "238",
                "categoryName":  "",
            }
        )

    payload = {
        "operationType": "104",   # Opening stock
        "goodsTypeCode": "1",
        "supplierName":  "",
        "supplierTin":   "",
        "referenceNo":   doc.name,
        "currency":      "UGX",
        "remarks":       doc.get("remarks") or "Opening Stock",
        "operator":      frappe.session.user,
        "deviceNo":      settings.device_no,
        "issuedDate":    str(doc.posting_date) + " 00:00:00",
        "goodsDetails":  goods_details,
        "summary": {
            "totalAmount": str(round(total, 2)),
            "itemCount":   str(len(goods_details)),
            "remarks":     "",
        },
    }

    result     = client.call("T131", payload, doc_type="Stock Reconciliation", doc_name=doc.name)
    invoice_id = result.get("invoiceId", "")
    doc.db_set("efris_invoice_id", invoice_id, update_modified=False)
    doc.db_set("efris_status", "Submitted", update_modified=False)
    frappe.msgprint(f"Opening stock submitted to EFRIS. ID: {invoice_id}")
