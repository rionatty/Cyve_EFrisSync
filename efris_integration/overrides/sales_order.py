import frappe


def on_submit(doc, method):
    settings = frappe.get_single("EFRIS Settings")
    if not settings.enabled or not settings.auto_submit_sales_order:
        return
    try:
        from efris_integration.api.sales_order import submit_sales_order
        submit_sales_order(doc.name)
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), f"EFRIS Sales Order Failed: {doc.name}")
        frappe.msgprint(f"EFRIS SO submission failed: {e}.", indicator="orange", alert=True)
