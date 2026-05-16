"""
Purchase Receipt hook — equivalent to SAP B1's GRPO (Goods Receipt PO).
Submits to EFRIS T131 with stock-in option 102 (Local Purchase) by default.
For imports, set efris_stock_in_option = "101" on the Purchase Receipt.
"""

import frappe
from efris_integration.utils.helpers import get_efris_settings


def on_submit(doc, method):
    try:
        get_efris_settings(doc.company)
    except Exception:
        return  # EFRIS not configured for this company — skip silently
    try:
        from efris_integration.api.stock import submit_purchase_receipt
        submit_purchase_receipt(doc.name)
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), f"EFRIS Purchase Receipt Failed: {doc.name}")
        frappe.msgprint(f"EFRIS purchase receipt failed: {e}.", indicator="orange", alert=True)
