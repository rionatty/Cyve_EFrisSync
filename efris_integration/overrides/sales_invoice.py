"""
Sales Invoice hooks — triggered on submit and cancel.

Submit: auto-submit to EFRIS if auto_submit_sales_invoice = 1 in settings.
        Credit notes (is_return=1) go to T110; regular invoices go to T108.
Cancel: EFRIS does not support cancellation; we only clear local EFRIS fields
        and log a warning. The user must issue a credit note instead.
"""

import frappe


def on_submit(doc, method):
    settings = frappe.get_single("EFRIS Settings")
    if not settings.enabled:
        return

    if doc.is_return:
        if settings.auto_submit_credit_notes:
            _submit_credit_note(doc)
    else:
        if settings.auto_submit_sales_invoice:
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
