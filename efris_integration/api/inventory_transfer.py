"""
Inter-Branch Inventory Transfer → EFRIS

Mirrors: InventoryTransfer.cs

In SAP B1, an Inventory Transfer between branches triggers EFRIS when
U_OT_MULTIBRNCH = 'Y' and U_OT_CSTCTSF = 'Y'.

In ERPNext, this maps to a Stock Entry with type "Material Transfer" where:
  - Same source/target EFRIS branch   → no EFRIS submission required
  - Different source/target branches  → T115 (Goods Issue) from source branch
                                         + T114 (Goods Receipt, option 105) to target branch

Both calls are made sequentially; if T114 fails after T115 we log the mismatch
so it can be reconciled manually.
"""

from datetime import datetime

import frappe

from efris_integration.api.base import EFRISClient
from efris_integration.utils.helpers import (
    efris_num,
    get_efris_settings,
    get_item_efris_fields,
    map_uom,
    safe_float,
)


@frappe.whitelist()
def submit_inter_branch_transfer(stock_entry_name: str) -> dict:
    doc = frappe.get_doc("Stock Entry", stock_entry_name)

    if doc.stock_entry_type != "Material Transfer":
        frappe.throw("Only Material Transfer entries can be submitted via this interface.")
    if doc.docstatus != 1:
        frappe.throw("Stock Entry must be submitted first.")
    if doc.get("efris_invoice_id"):
        frappe.throw(
            f"Stock Entry {doc.name} has already been submitted to URA EFRIS "
            f"(ID: {doc.efris_invoice_id}). To re-submit, clear the URA Reference No "
            "and EFRIS Status on the URA tab first."
        )

    if not doc.items:
        frappe.throw("No items found on this Stock Entry.")

    settings = get_efris_settings(doc.company)

    # ── Resolve source and target branch IDs ────────────────────────────────
    source_wh    = doc.items[0].s_warehouse
    target_wh    = doc.items[0].t_warehouse
    source_branch = _get_branch(source_wh)
    target_branch = _get_branch(target_wh)

    source_bid = (source_branch.get("efris_branch_id") or "").strip()
    target_bid = (target_branch.get("efris_branch_id") or "").strip()

    # ── Same-branch shortcut ─────────────────────────────────────────────────
    # Internal transfers within one EFRIS branch are not reported to URA.
    if source_bid and source_bid == target_bid:
        frappe.msgprint(
            f"No EFRIS submission required — source warehouse <b>{source_wh}</b> "
            f"and target warehouse <b>{target_wh}</b> both belong to the same "
            f"EFRIS branch (<b>{source_bid}</b>). "
            "Internal transfers within a branch are not reported to URA.",
            title="Same-Branch Transfer — Skipped",
            indicator="blue",
        )
        return {"skipped": True, "reason": "same_branch", "branch_id": source_bid}

    # ── Different branches — validate branch IDs are set ────────────────────
    if settings.multi_branch:
        if not source_bid:
            frappe.throw(
                f"Multi-branch is enabled but warehouse <b>{source_wh}</b> has no "
                "EFRIS Branch ID. Please set it on the Warehouse master before submitting."
            )
        if not target_bid:
            frappe.throw(
                f"Multi-branch is enabled but warehouse <b>{target_wh}</b> has no "
                "EFRIS Branch ID. Please set it on the Warehouse master before submitting."
            )

    client       = EFRISClient(company=doc.company)
    goods_details = _build_goods(doc)
    total        = sum(safe_float(i.amount) for i in doc.items)
    issued_date  = str(doc.posting_date) + " 00:00:00"

    # ── T115: Goods Issue from source branch ─────────────────────────────────
    t115_payload = {
        "operationType": "203",          # Inter-branch transfer out
        "goodsTypeCode": "1",
        "referenceNo":   doc.name,
        "currency":      "UGX",
        "remarks":       f"Inter-branch transfer to {target_branch.get('efris_branch_name') or target_wh}",
        "operator":      frappe.session.user,
        "deviceNo":      settings.device_no,
        "issuedDate":    issued_date,
        "goodsDetails":  goods_details,
        "summary": {
            "totalAmount": efris_num(total, decimal_places=2),
            "itemCount":   str(len(goods_details)),
        },
        "branchId": source_bid,
    }
    t115_result = client.call(
        "T115", t115_payload,
        encrypt=True, sign=True,
        doc_type="Stock Entry", doc_name=doc.name
    )

    # ── T114: Goods Receipt at target branch ─────────────────────────────────
    t114_payload = {
        "operationType": "105",          # Transfer-in
        "goodsTypeCode": "1",
        "supplierName":  source_branch.get("efris_branch_name") or settings.branch_name or "",
        "supplierTin":   settings.tin or "",
        "referenceNo":   doc.name,
        "currency":      "UGX",
        "remarks":       f"Inter-branch transfer from {source_branch.get('efris_branch_name') or source_wh}",
        "operator":      frappe.session.user,
        "deviceNo":      settings.device_no,
        "issuedDate":    issued_date,
        "goodsDetails":  goods_details,
        "summary": {
            "totalAmount": efris_num(total, decimal_places=2),
            "itemCount":   str(len(goods_details)),
        },
        "branchId": target_bid,
    }
    t114_result = client.call(
        "T114", t114_payload,
        encrypt=True, sign=True,
        doc_type="Stock Entry", doc_name=doc.name + "-IN"
    )

    issue_id   = t115_result.get("invoiceId", "")
    receipt_id = t114_result.get("invoiceId", "")

    # ── Persist result ───────────────────────────────────────────────────────
    combined_id = f"OUT:{issue_id} | IN:{receipt_id}"
    doc.db_set("efris_invoice_id",   combined_id,      update_modified=False)
    doc.db_set("efris_status",       "Submitted",       update_modified=False)
    doc.db_set("efris_submitted_at", datetime.now(),    update_modified=False)

    frappe.msgprint(
        f"Inter-branch transfer submitted to EFRIS.<br>"
        f"<b>Source branch:</b> {source_bid} &rarr; "
        f"<b>Target branch:</b> {target_bid}<br>"
        f"<b>T115 Issue ID:</b> {issue_id}<br>"
        f"<b>T114 Receipt ID:</b> {receipt_id}",
        title="EFRIS Inter-Branch Transfer",
        indicator="green",
    )
    return {"issue_id": issue_id, "receipt_id": receipt_id}


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────

def _build_goods(doc) -> list:
    company = getattr(doc, "company", "") or ""
    goods = []
    for idx, line in enumerate(doc.items, 1):
        item_fields = get_item_efris_fields(line.item_code)
        rate = safe_float(
            line.basic_rate if hasattr(line, "basic_rate") else line.get("rate", 0)
        )
        goods.append({
            "item":          line.item_name or line.item_code,
            "itemCode":      line.item_code,
            "qty":           efris_num(line.qty,   decimal_places=6),
            "unitOfMeasure": map_uom(line.uom, company),
            "unitPrice":     efris_num(rate,        decimal_places=2),
            "total":         efris_num(line.amount, decimal_places=2),
            "orderNumber":   str(idx),
            "categoryId":    item_fields.get("efris_goods_category_id") or "238",
            "categoryName":  line.item_group or "",
        })
    return goods


def _get_branch(warehouse_name: str) -> dict:
    if not warehouse_name:
        return {}
    return frappe.db.get_value(
        "Warehouse", warehouse_name,
        ["efris_branch_id", "efris_branch_name", "efris_branch_code"],
        as_dict=True,
    ) or {}
