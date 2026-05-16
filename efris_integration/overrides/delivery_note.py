import frappe
from efris_integration.utils.helpers import get_efris_settings


def on_submit(doc, method):
    try:
        get_efris_settings(doc.company)
    except Exception:
        return  # EFRIS not configured for this company — skip silently
    try:
        from efris_integration.api.sales_order import submit_delivery_note
        submit_delivery_note(doc.name)
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), f"EFRIS Delivery Note Failed: {doc.name}")
        frappe.msgprint(f"EFRIS Delivery Note submission failed: {e}.", indicator="orange", alert=True)
