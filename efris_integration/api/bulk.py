"""
Bulk EFRIS submission — mirrors BulkPosting.cs

Allows submitting a batch of documents (e.g. historical invoices) to EFRIS in one go.
"""

import json

import frappe
from frappe.utils import strip_html

from efris_integration.api.invoice import submit_sales_invoice
from efris_integration.api.credit_note import submit_credit_note
from efris_integration.api.stock import submit_purchase_receipt, submit_stock_entry


@frappe.whitelist()
def bulk_submit(doctype: str, names: list | str) -> dict:
    """
    Submit multiple documents to EFRIS.
    names can be a JSON list string or a Python list.
    """

    if isinstance(names, str):
        names = json.loads(names)

    handler_map = {
        "Sales Invoice": _handle_sales_invoice,
        "Purchase Receipt": _handle_purchase_receipt,
        "Stock Entry": _handle_stock_entry,
    }

    handler = handler_map.get(doctype)
    if not handler:
        frappe.throw(f"Bulk submission not supported for {doctype}.")

    res = run_bulk(names, handler)
    return {"success": [d["name"] for d in res["done"]], "failed": res["failed"]}


@frappe.whitelist()
def bulk_upload_items(names, company: str, mode: str = "register") -> dict:
    """EFRIS Item Upload report: register new goods (T130 add) or update registered ones."""
    from efris_integration.api.goods import _modify_goods, _register_goods

    upload = _register_goods if mode == "register" else _modify_goods
    return run_bulk(names, lambda name: upload(frappe.get_doc("Item", name), company))


@frappe.whitelist()
def bulk_verify_customers(names, company: str, update_name: int = 0) -> dict:
    """EFRIS Customer TIN Verification report: T119 per customer (SAP BulkBPVerify)."""
    from efris_integration.api.customer import verify_customer

    return run_bulk(names, lambda name: verify_customer(name, company, update_name))


def run_bulk(names, fn) -> dict:
    """
    Run fn(name) for each name, committing after each one so finished work is
    kept if a later one fails. fn may return {"skipped": reason}.
    Returns {"done": [...], "failed": [...], "skipped": [...]}; the per-document
    pop-ups are dropped (the caller shows one summary).
    """
    names = json.loads(names) if isinstance(names, str) else (names or [])
    out = {"done": [], "failed": [], "skipped": []}
    for name in names:
        try:
            result = fn(name)
            if isinstance(result, dict) and result.get("skipped"):
                out["skipped"].append({"name": name, "reason": result["skipped"]})
            else:
                out["done"].append({"name": name})
            frappe.db.commit()
        except Exception as exc:
            frappe.log_error(frappe.get_traceback(), f"EFRIS bulk: {name}")
            out["failed"].append({"name": name, "error": strip_html(str(exc))[:300]})
        finally:
            frappe.clear_messages()
    return out


def _handle_sales_invoice(name: str):
    doc = frappe.get_doc("Sales Invoice", name)
    if doc.is_return:
        if not doc.get("efris_cn_reference_no"):
            submit_credit_note(name, show_message=False)
    elif not doc.get("efris_invoice_id"):
        submit_sales_invoice(name)


def _handle_purchase_receipt(name: str):
    if frappe.db.get_value("Purchase Receipt", name, "efris_status") == "Submitted":
        return
    submit_purchase_receipt(name)


def _handle_stock_entry(name: str):
    if frappe.db.get_value("Stock Entry", name, "efris_status") == "Submitted":
        return
    submit_stock_entry(name)
