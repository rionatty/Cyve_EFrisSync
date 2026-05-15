import frappe


def on_submit(doc, method):
    settings = frappe.get_single("EFRIS Settings")
    if not settings.enabled or not settings.auto_submit_delivery_note:
        return
    try:
        from efris_integration.api.sales_order import submit_delivery_note
        submit_delivery_note(doc.name)
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), f"EFRIS Delivery Note Failed: {doc.name}")
        frappe.msgprint(f"EFRIS Delivery Note submission failed: {e}.", indicator="orange", alert=True)
