"""
Purchase Receipt hook — equivalent to SAP B1's GRPO (Goods Receipt PO).
Submits to EFRIS T114 with stock-in option 102 (Local Purchase) by default.
For imports, set efris_stock_in_option = "101" on the Purchase Receipt.
"""

import frappe


def on_submit(doc, method):
    settings = frappe.get_single("EFRIS Settings")
    if not settings.enabled or not settings.auto_submit_purchase_receipt:
        return
    try:
        from efris_integration.api.stock import submit_purchase_receipt
        submit_purchase_receipt(doc.name)
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), f"EFRIS Purchase Receipt Failed: {doc.name}")
        frappe.msgprint(f"EFRIS purchase receipt failed: {e}.", indicator="orange", alert=True)
