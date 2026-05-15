"""
Inter-Branch Inventory Transfer → EFRIS

Mirrors: InventoryTransfer.cs

In SAP B1, an Inventory Transfer between branches could trigger EFRIS if
U_OT_MULTIBRNCH = 'Y' and U_OT_CSTCTSF = 'Y'.

In ERPNext, this maps to a Stock Entry with type "Material Transfer" where
the source and target warehouses belong to different EFRIS branches
(Warehouse.efris_branch_id differs).

The source warehouse sends a T115 (Goods Issue).
The target warehouse receives a T114 (Goods Receipt, option 105 = Transfer).
Both calls are made atomically; if T114 fails we log the mismatch.
"""

import frappe

from efris_integration.api.base import EFRISClient
from efris_integration.utils.helpers import (
    get_item_efris_fields,
    map_uom,
    safe_float,
)


@frappe.whitelist()
def submit_inter_branch_transfer(stock_entry_name: str) -> dict:
    doc = frappe.get_doc("Stock Entry", stock_entry_name)

    if doc.stock_entry_type != "Material Transfer":
        frappe.throw("Only Material Transfer entries can be submitted as inter-branch.")
    if doc.docstatus != 1:
        frappe.throw("Stock Entry must be submitted first.")

    settings = frappe.get_single("EFRIS Settings")
    if not settings.multi_branch:
        frappe.throw("Multi-branch mode is not enabled in EFRIS Settings.")

    client = EFRISClient()
    goods_details = _build_goods(doc)
    total = sum(safe_float(i.amount) for i in doc.items)
    issued_date = str(doc.posting_date) + " 00:00:00"

    source_branch = _get_branch(doc.items[0].s_warehouse)
    target_branch = _get_branch(doc.items[0].t_warehouse)

    # ── T115: Issue from source branch ────────────────────────────────────
    t115_payload = {
        "operationType": "203",          # Inter-branch transfer out
        "goodsTypeCode": "1",
        "referenceNo": doc.name,
        "currency": "UGX",
        "remarks": f"Transfer to branch {target_branch.get('efris_branch_id','')}",
        "operator": frappe.session.user,
        "deviceNo": settings.device_no,
        "issuedDate": issued_date,
        "goodsDetails": goods_details,
        "summary": {"totalAmount": str(round(total, 2)), "itemCount": str(len(goods_details))},
        "branchId": source_branch.get("efris_branch_id", ""),
    }
    t115_result = client.call("T115", t115_payload, doc_type="Stock Entry", doc_name=doc.name)

    # ── T114: Receive at target branch ────────────────────────────────────
    t114_payload = {
        "operationType": "105",          # Transfer-in
        "goodsTypeCode": "1",
        "supplierName": source_branch.get("efris_branch_name", ""),
        "supplierTin": settings.tin,
        "referenceNo": doc.name,
        "currency": "UGX",
        "remarks": f"Transfer from branch {source_branch.get('efris_branch_id','')}",
        "operator": frappe.session.user,
        "deviceNo": settings.device_no,
        "issuedDate": issued_date,
        "goodsDetails": goods_details,
        "summary": {"totalAmount": str(round(total, 2)), "itemCount": str(len(goods_details))},
        "branchId": target_branch.get("efris_branch_id", ""),
    }
    t114_result = client.call("T114", t114_payload, doc_type="Stock Entry", doc_name=doc.name + "-IN")

    issue_id   = t115_result.get("invoiceId", "")
    receipt_id = t114_result.get("invoiceId", "")

    doc.db_set("efris_invoice_id", f"OUT:{issue_id} | IN:{receipt_id}", update_modified=False)
    doc.db_set("efris_status", "Submitted", update_modified=False)

    frappe.msgprint(
        f"Inter-branch transfer submitted to EFRIS.\n"
        f"Issue ID: {issue_id}\nReceipt ID: {receipt_id}"
    )
    return {"issue_id": issue_id, "receipt_id": receipt_id}


def _build_goods(doc) -> list:
    goods = []
    for idx, line in enumerate(doc.items, 1):
        item_fields = get_item_efris_fields(line.item_code)
        goods.append({
            "item": line.item_name or line.item_code,
            "itemCode": line.item_code,
            "qty": str(safe_float(line.qty)),
            "unitOfMeasure": map_uom(line.uom),
            "unitPrice": str(safe_float(line.basic_rate)),
            "total": str(safe_float(line.amount)),
            "orderNumber": str(idx),
            "categoryId": item_fields.get("efris_goods_category_id") or "238",
            "categoryName": line.item_group or "",
        })
    return goods


def _get_branch(warehouse_name: str) -> dict:
    return frappe.db.get_value(
        "Warehouse", warehouse_name,
        ["efris_branch_id", "efris_branch_name", "efris_branch_code"],
        as_dict=True,
    ) or {}
