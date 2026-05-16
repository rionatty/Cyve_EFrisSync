"""
Stock Entry hook — dispatches to the correct EFRIS endpoint based on stock_entry_type.

Type → EFRIS mapping
──────────────────────────────────────────────────────────────────────────────
Material Receipt                   → T131  (stock-in, option from doc field)
Material Issue                     → T131  (stock-out)
Send to Subcontractor              → T131  (stock-out)
Material Transfer for Manufacture  → T131  (Issue From Production, op 202)
Manufacture                        → T131  (Receipt From Production, op 103)
                                     + T131 for consumed RM if issue_from_production ON
Material Transfer                  → T131 + T131 if stock_transfer flag AND inter-branch
──────────────────────────────────────────────────────────────────────────────

Mirrors: GoodsReceipt.cs, GoodsIssue.cs, IssueFromProduction.cs,
         ReceiptFromProduction.cs, InventoryTransfer.cs
"""

import frappe
from efris_integration.utils.helpers import get_efris_settings


def on_submit(doc, method):
    try:
        settings = get_efris_settings(doc.company)
    except Exception:
        return  # EFRIS not configured for this company — skip silently

    t = doc.stock_entry_type

    try:
        if t == "Material Receipt":
            _run_stock_receipt(doc)

        elif t in ("Material Issue", "Send to Subcontractor"):
            _run_stock_issue(doc)

        elif t == "Material Transfer for Manufacture":
            # Issue From Production — raw materials consumed by work order
            if settings.issue_from_production:
                _run_stock_issue(doc)

        elif t == "Manufacture":
            # Receipt From Production — finished goods produced
            if settings.receipt_from_production:
                _run_stock_receipt(doc)
            # Also submit consumed raw materials as goods issue if enabled
            if settings.issue_from_production:
                _run_manufacture_rm_issue(doc, settings)

        elif t == "Material Transfer":
            # Inter-branch only, and only if stock_transfer flag is ON
            if settings.stock_transfer and settings.multi_branch:
                if _is_inter_branch(doc):
                    _run_inter_branch(doc)

    except Exception as exc:
        frappe.log_error(frappe.get_traceback(), f"EFRIS Stock Entry ({t}): {doc.name}")
        frappe.msgprint(
            f"EFRIS submission failed for {doc.name}: {exc}\n"
            "You can retry manually from the Stock Entry form.",
            indicator="orange",
            alert=True,
        )


# ─── Handlers ────────────────────────────────────────────────────────────

def _run_stock_receipt(doc):
    from efris_integration.api.stock import submit_stock_receipt
    submit_stock_receipt(doc.name)


def _run_stock_issue(doc):
    from efris_integration.api.stock import submit_stock_issue
    submit_stock_issue(doc.name)


def _run_manufacture_rm_issue(doc, settings):
    """Submit raw material (source warehouse) lines of a Manufacture entry."""
    from efris_integration.api.stock import _post_t131_issue
    # Only submit if there are consumed (source-warehouse) lines
    consumed = [i for i in doc.items if i.s_warehouse and not i.t_warehouse]
    if consumed:
        _post_t131_issue(doc, settings, "Stock Entry", doc.name + "-RM")


def _run_inter_branch(doc):
    from efris_integration.api.inventory_transfer import submit_inter_branch_transfer
    submit_inter_branch_transfer(doc.name)


# ─── Branch detection ─────────────────────────────────────────────────────

def _is_inter_branch(doc) -> bool:
    """True when source and target warehouses carry different EFRIS branch IDs."""
    src_branches = {
        frappe.db.get_value("Warehouse", i.s_warehouse, "efris_branch_id")
        for i in doc.items if i.s_warehouse
    }
    tgt_branches = {
        frappe.db.get_value("Warehouse", i.t_warehouse, "efris_branch_id")
        for i in doc.items if i.t_warehouse
    }
    return bool(src_branches - tgt_branches or tgt_branches - src_branches)
