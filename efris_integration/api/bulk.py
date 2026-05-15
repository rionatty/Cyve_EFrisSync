"""
Bulk EFRIS submission — mirrors BulkPosting.cs

Allows submitting a batch of documents (e.g. historical invoices) to EFRIS in one go.
"""

import frappe

from efris_integration.api.invoice import submit_sales_invoice
from efris_integration.api.credit_note import submit_credit_note
from efris_integration.api.stock import submit_purchase_receipt, submit_stock_receipt, submit_stock_issue


@frappe.whitelist()
def bulk_submit(doctype: str, names: list | str) -> dict:
    """
    Submit multiple documents to EFRIS.
    names can be a JSON list string or a Python list.
    """
    import json

    if isinstance(names, str):
        names = json.loads(names)

    results = {"success": [], "failed": []}
    handler_map = {
        "Sales Invoice": _handle_sales_invoice,
        "Purchase Receipt": _handle_purchase_receipt,
        "Stock Entry": _handle_stock_entry,
    }

    handler = handler_map.get(doctype)
    if not handler:
        frappe.throw(f"Bulk submission not supported for {doctype}.")

    for name in names:
        try:
            handler(name)
            results["success"].append(name)
        except Exception as e:
            results["failed"].append({"name": name, "error": str(e)})
            frappe.log_error(frappe.get_traceback(), f"EFRIS Bulk {doctype}: {name}")

    frappe.db.commit()
    frappe.msgprint(
        f"Bulk EFRIS submission complete. "
        f"Success: {len(results['success'])}, Failed: {len(results['failed'])}."
    )
    return results


def _handle_sales_invoice(name: str):
    doc = frappe.get_doc("Sales Invoice", name)
    if doc.get("efris_invoice_id"):
        return  # already done
    if doc.is_return:
        submit_credit_note(name)
    else:
        submit_sales_invoice(name)


def _handle_purchase_receipt(name: str):
    doc = frappe.get_doc("Purchase Receipt", name)
    if doc.get("efris_invoice_id"):
        return
    submit_purchase_receipt(name)


def _handle_stock_entry(name: str):
    doc = frappe.get_doc("Stock Entry", name)
    if doc.get("efris_invoice_id"):
        return
    if doc.stock_entry_type in ("Material Receipt", "Manufacture"):
        submit_stock_receipt(name)
    elif doc.stock_entry_type in ("Material Issue", "Material Transfer for Manufacture", "Send to Subcontractor"):
        submit_stock_issue(name)
