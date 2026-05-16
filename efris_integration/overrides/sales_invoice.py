"""
Sales Invoice hooks — triggered on submit and cancel.

Submit: auto-submit to EFRIS if EFRIS Settings are configured for the company.
        Credit notes (is_return=1) go to T110; regular invoices go to T109.
Cancel: EFRIS does not support cancellation; we only clear local EFRIS fields
        and log a warning. The user must issue a credit note instead.
"""

import frappe
from efris_integration.utils.helpers import get_efris_settings


def on_submit(doc, method):
    try:
        settings = get_efris_settings(doc.company)
    except Exception:
        return  # EFRIS not configured for this company — skip silently

    if doc.is_return:
        _submit_credit_note(doc)
    else:
        _submit_invoice(doc)


def on_cancel(doc, method):
    if doc.get("efris_invoice_id"):
        frappe.msgprint(
            "Warning: This invoice was submitted to EFRIS. "
            "EFRIS does not support cancellation — please issue a Credit Note instead.",
            indicator="orange",
            alert=True,
        )


# ─── Internal ────────────────────────────────────────────────────────────────

def _submit_invoice(doc):
    try:
        from efris_integration.api.invoice import submit_sales_invoice
        submit_sales_invoice(doc.name)
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), f"EFRIS Invoice Submission Failed: {doc.name}")
        frappe.msgprint(
            f"EFRIS submission failed: {e}. You can retry manually from the invoice.",
            indicator="red",
            alert=True,
        )


def _submit_credit_note(doc):
    try:
        from efris_integration.api.credit_note import submit_credit_note
        submit_credit_note(doc.name)
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), f"EFRIS Credit Note Submission Failed: {doc.name}")
        frappe.msgprint(
            f"EFRIS credit note submission failed: {e}.",
            indicator="red",
            alert=True,
        )
