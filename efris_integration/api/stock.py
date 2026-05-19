"""
Stock movement submissions to EFRIS.

T114 — Goods Receipt (Stock In)          → ERPNext Stock Entry (Material Receipt)
                                          → ERPNext Purchase Receipt
                                          → ERPNext Stock Entry (Manufacture) [Receipt From Production]

T115 — Goods Issue (Stock Out)           → ERPNext Stock Entry (Material Issue)
                                          → ERPNext Stock Entry (Material Transfer for Manufacture) [Issue From Production]
                                          → ERPNext Delivery Note (when stock-out linked to sale)

T119 — Goods Return                      → ERPNext Purchase Return
                                          → ERPNext Sales Return (Stock Entry)

SAP B1 Goods Receipt stock-in options:
  101 = Import
  102 = Local Purchase
  103 = Manufacture / Receipt From Production
  104 = Opening Stock

ERPNext Stock Entry type → EFRIS mapping:
  "Material Receipt"                     → T114, option determined by efris_stock_in_option field
  "Material Issue"                       → T115
  "Material Transfer"                    → Skip (internal; no EFRIS required unless inter-branch)
  "Material Transfer for Manufacture"    → T115 (Issue From Production)
  "Manufacture"                          → T114 option 103 for FG lines, T115 for consumed RM lines
  "Send to Subcontractor"                → T115

Mirrors: GoodsReceipt.cs, GoodsIssue.cs, GoodsReturn.cs, IssueFromProduction.cs,
         ReceiptFromProduction.cs, GRPO.cs, T114.cs, T115.cs, T119.cs
"""

from datetime import datetime

import frappe

from efris_integration.api.base import EFRISClient, EFRISAPIError
from efris_integration.utils.helpers import (
    get_efris_settings,
    get_item_efris_fields,
    get_supplier_efris_fields,
    map_uom,
    resolve_branch_for_doc,
    safe_float,
    safe_str,
)

# ─── Stock-in option codes (mirrors SAP B1 UDF U_OT_OPTION) ─────────────────
STOCK_IN_IMPORT = "101"
STOCK_IN_LOCAL = "102"
STOCK_IN_MANUFACTURE = "103"
STOCK_IN_OPENING = "104"
STOCK_IN_TRANSFER = "105"


# ────────────────────────────────────────────────────────────────────────────
# T114  —  Goods Receipt / Stock In
# ────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def submit_stock_receipt(stock_entry_name: str) -> dict:
    """
    Submit a Stock Entry (Material Receipt) or Manufacture FG to EFRIS T114.
    """
    doc = frappe.get_doc("Stock Entry", stock_entry_name)
    if doc.docstatus != 1:
        frappe.throw("Stock Entry must be submitted first.")
    if doc.get("efris_invoice_id"):
        frappe.throw(f"Already submitted: {doc.efris_invoice_id}")

    return _post_t114(doc, "Stock Entry", stock_entry_name)


@frappe.whitelist()
def submit_purchase_receipt(receipt_name: str) -> dict:
    """
    Submit a Purchase Receipt (GRPO equivalent) to EFRIS T114 with option 102 (Local) or 101 (Import).
    """
    doc = frappe.get_doc("Purchase Receipt", receipt_name)
    if doc.docstatus != 1:
        frappe.throw("Purchase Receipt must be submitted first.")
    if doc.get("efris_invoice_id"):
        frappe.throw(f"Already submitted: {doc.efris_invoice_id}")

    return _post_t114_purchase_receipt(doc)


def _post_t114(doc, doctype: str, docname: str) -> dict:
    """Build T114 payload for Stock Entry."""
    settings = get_efris_settings(doc.company)
    resolve_branch_for_doc(doc, settings)   # multi-branch: use target warehouse branch
    client   = EFRISClient(company=doc.company)
    client.settings.branch_id   = settings.branch_id
    client.settings.branch_name = settings.branch_name

    stock_in_option = doc.get("efris_stock_in_option") or STOCK_IN_LOCAL

    # For Manufacture entries, only send the finished goods lines (target warehouse)
    if doc.stock_entry_type == "Manufacture":
        items = [i for i in doc.items if i.t_warehouse and not i.s_warehouse]
        stock_in_option = STOCK_IN_MANUFACTURE
    elif doc.stock_entry_type == "Material Transfer for Manufacture":
        # This is Issue From Production → T115
        return _post_t115_manufacture_issue(doc, settings, doctype, docname)
    else:
        items = [i for i in doc.items if i.t_warehouse]

    goods_details = _build_stock_goods(items, doc)
    total = sum(safe_float(i.amount) for i in items)

    payload = {
        "operationType": stock_in_option,
        "goodsTypeCode": "1",
        "supplierName": doc.get("efris_supplier_name") or "",
        "supplierTin": doc.get("efris_supplier_tin") or "",
        "referenceNo": doc.get("efris_reference_no") or doc.name,
        "currency": "UGX",
        "remarks": doc.get("remarks") or "",
        "operator": frappe.session.user,
        "deviceNo": settings.device_no,
        "issuedDate": str(doc.posting_date) + " 00:00:00",
        "goodsDetails": goods_details,
        "summary": {
            "totalAmount": str(round(total, 2)),
            "itemCount": str(len(goods_details)),
            "remarks": doc.get("remarks") or "",
        },
    }

    result = client.call("T114", payload, encrypt=True, sign=True, doc_type=doctype, doc_name=docname)
    _save_efris_result(doc, result)
    frappe.msgprint(f"Stock Receipt submitted to EFRIS. ID: {result.get('invoiceId','')}")
    return result


def _post_t114_purchase_receipt(doc) -> dict:
    """Build T114 payload for Purchase Receipt."""
    settings = get_efris_settings(doc.company)
    resolve_branch_for_doc(doc, settings)   # multi-branch: use receiving warehouse branch
    client   = EFRISClient(company=doc.company)
    client.settings.branch_id   = settings.branch_id
    client.settings.branch_name = settings.branch_name
    supplier_fields = get_supplier_efris_fields(doc.supplier)

    stock_in_option = doc.get("efris_stock_in_option") or STOCK_IN_LOCAL
    goods_details = _build_stock_goods(doc.items, doc)
    total = sum(safe_float(i.amount) for i in doc.items)

    payload = {
        "operationType": stock_in_option,
        "goodsTypeCode": "1",
        "supplierName": doc.supplier_name or "",
        "supplierTin": safe_str(supplier_fields.get("tax_id")),
        "referenceNo": doc.name,
        "currency": doc.currency or "UGX",
        "remarks": doc.get("remarks") or "",
        "operator": frappe.session.user,
        "deviceNo": settings.device_no,
        "issuedDate": str(doc.posting_date) + " 00:00:00",
        "goodsDetails": goods_details,
        "summary": {
            "totalAmount": str(round(total, 2)),
            "itemCount": str(len(goods_details)),
            "remarks": "",
        },
    }

    result = client.call("T114", payload, encrypt=True, sign=True, doc_type="Purchase Receipt", doc_name=doc.name)
    _save_efris_result(doc, result)
    frappe.msgprint(f"Purchase Receipt submitted to EFRIS. ID: {result.get('invoiceId','')}")
    return result


# ────────────────────────────────────────────────────────────────────────────
# T115  —  Goods Issue / Stock Out
# ────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def submit_stock_issue(stock_entry_name: str) -> dict:
    """Submit a Stock Entry (Material Issue or Send to Subcontractor) to EFRIS T115."""
    doc = frappe.get_doc("Stock Entry", stock_entry_name)
    if doc.docstatus != 1:
        frappe.throw("Stock Entry must be submitted first.")
    if doc.get("efris_invoice_id"):
        frappe.throw(f"Already submitted: {doc.efris_invoice_id}")

    settings = get_efris_settings(doc.company)
    return _post_t115(doc, settings, "Stock Entry", stock_entry_name)


def _post_t115(doc, settings, doctype: str, docname: str) -> dict:
    resolve_branch_for_doc(doc, settings)   # multi-branch: use source warehouse branch
    client = EFRISClient(company=doc.company)
    client.settings.branch_id   = settings.branch_id
    client.settings.branch_name = settings.branch_name
    items = [i for i in doc.items if i.s_warehouse]
    goods_details = _build_stock_goods(items, doc)
    total = sum(safe_float(i.amount) for i in items)

    payload = {
        "operationType": "201",  # Normal goods issue
        "goodsTypeCode": "1",
        "recipientName": doc.get("efris_recipient_name") or "",
        "recipientTin": doc.get("efris_recipient_tin") or "",
        "referenceNo": doc.name,
        "currency": "UGX",
        "remarks": doc.get("remarks") or "",
        "operator": frappe.session.user,
        "deviceNo": settings.device_no,
        "issuedDate": str(doc.posting_date) + " 00:00:00",
        "goodsDetails": goods_details,
        "summary": {
            "totalAmount": str(round(total, 2)),
            "itemCount": str(len(goods_details)),
            "remarks": "",
        },
    }

    result = client.call("T115", payload, encrypt=True, sign=True, doc_type=doctype, doc_name=docname)
    _save_efris_result(doc, result)
    frappe.msgprint(f"Stock Issue submitted to EFRIS. ID: {result.get('invoiceId','')}")
    return result


def _post_t115_manufacture_issue(doc, settings, doctype: str, docname: str) -> dict:
    """Issue From Production: raw materials transferred to work order → T115 with operationType 202."""
    client = EFRISClient(company=doc.company)
    items = [i for i in doc.items if i.s_warehouse]
    goods_details = _build_stock_goods(items, doc)
    total = sum(safe_float(i.amount) for i in items)

    payload = {
        "operationType": "202",  # Issue from production
        "goodsTypeCode": "1",
        "referenceNo": doc.work_order or doc.name,
        "currency": "UGX",
        "remarks": f"Issue for WO: {doc.work_order or ''}",
        "operator": frappe.session.user,
        "deviceNo": settings.device_no,
        "issuedDate": str(doc.posting_date) + " 00:00:00",
        "goodsDetails": goods_details,
        "summary": {
            "totalAmount": str(round(total, 2)),
            "itemCount": str(len(goods_details)),
            "remarks": "",
        },
    }

    result = client.call("T115", payload, encrypt=True, sign=True, doc_type=doctype, doc_name=docname)
    _save_efris_result(doc, result)
    frappe.msgprint(f"Issue From Production submitted to EFRIS. ID: {result.get('invoiceId','')}")
    return result


# ────────────────────────────────────────────────────────────────────────────
# T119  —  Goods Return
# ────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def submit_goods_return(doctype: str, docname: str) -> dict:
    """
    Submit a goods return to EFRIS T119.
    Handles:
      - Purchase Return (Purchase Receipt with is_return=1)
      - Sales Return via Stock Entry flagged as return
    """
    doc = frappe.get_doc(doctype, docname)
    if doc.docstatus != 1:
        frappe.throw("Document must be submitted first.")
    if doc.get("efris_invoice_id"):
        frappe.throw(f"Already submitted: {doc.efris_invoice_id}")

    settings = get_efris_settings(doc.company)
    resolve_branch_for_doc(doc, settings)   # multi-branch: resolve from document warehouse
    client   = EFRISClient(company=doc.company)
    client.settings.branch_id   = settings.branch_id
    client.settings.branch_name = settings.branch_name

    items = doc.items
    goods_details = _build_stock_goods(items, doc)
    total = sum(safe_float(i.amount) for i in items)

    ori_invoice_id = doc.get("efris_return_against_invoice_id") or ""

    payload = {
        "oriInvoiceId": ori_invoice_id,
        "returnReasonCode": doc.get("efris_return_reason_code") or "101",
        "returnReason": doc.get("efris_return_reason") or "Goods Return",
        "referenceNo": doc.name,
        "currency": "UGX",
        "remarks": doc.get("remarks") or "",
        "operator": frappe.session.user,
        "deviceNo": settings.device_no,
        "issuedDate": str(doc.posting_date) + " 00:00:00",
        "goodsDetails": goods_details,
        "summary": {
            "totalAmount": str(round(total, 2)),
            "itemCount": str(len(goods_details)),
        },
    }

    result = client.call("T119", payload, encrypt=True, sign=True, doc_type=doctype, doc_name=docname)
    _save_efris_result(doc, result)
    frappe.msgprint(f"Goods Return submitted to EFRIS. ID: {result.get('invoiceId','')}")
    return result


# ────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ────────────────────────────────────────────────────────────────────────────

def _build_stock_goods(items, doc) -> list:
    company = getattr(doc, "company", "") or ""
    goods = []
    for idx, line in enumerate(items, start=1):
        item_fields = get_item_efris_fields(line.item_code)
        goods.append(
            {
                "item": line.item_name or line.item_code,
                "itemCode": line.item_code,
                "qty": str(safe_float(line.qty)),
                "unitOfMeasure": map_uom(line.uom, company),
                "unitPrice": str(safe_float(line.basic_rate if hasattr(line, "basic_rate") else line.get("rate", 0))),
                "total": str(safe_float(line.amount)),
                "orderNumber": str(idx),
                "categoryId": item_fields.get("efris_goods_category_id") or "238",
                "categoryName": line.item_group or "",
            }
        )
    return goods


def _save_efris_result(doc, result: dict):
    invoice_id = result.get("invoiceId", "")
    doc.db_set("efris_invoice_id", invoice_id, update_modified=False)
    doc.db_set("efris_status", "Submitted", update_modified=False)
    doc.db_set("efris_submitted_at", datetime.now(), update_modified=False)


# Alias used by stock_entry.py override for manufacture RM issue
# Will be replaced with T131 implementation in the stock module rewrite.
_post_t131_issue = _post_t115
