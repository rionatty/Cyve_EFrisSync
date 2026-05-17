"""
Sales Invoice hooks — triggered on submit and cancel.

Submit: does NOT auto-submit to EFRIS.
        SAP B1 behaviour: the user manually clicks "Upload to EFRIS" on the
        form.  Auto-submitting on every document submit causes silent failures,
        sets efris_invoice_id unexpectedly, and hides the Upload button.

Cancel: EFRIS does not support cancellation; warn the user to issue a Credit Note.
"""

import frappe


def on_submit(doc, method):
    """
    Intentionally does NOT call EFRIS on submit.

    Reason: SAP B1 requires the user to explicitly click the Upload button.
    Auto-submitting silently sets efris_invoice_id, changes the button to
    "Refresh from EFRIS", and if it fails the user sees empty fields with
    no clear way to retry.  The manual button in sales_invoice.js handles this.
    """
    pass   # nothing to do here — user clicks "Upload to EFRIS" manually


def on_cancel(doc, method):
    """Warn the user that EFRIS invoices cannot be cancelled — use Credit Note."""
    if doc.get("efris_invoice_id"):
        frappe.msgprint(
            "⚠️  This invoice was already submitted to EFRIS "
            "(Invoice ID: {0}).<br><br>"
            "URA EFRIS does <b>not</b> support cancellation. "
            "Please issue a <b>Credit Note</b> (Return) against this invoice "
            "and upload it to EFRIS instead.".format(doc.efris_invoice_id),
            title="EFRIS: Cannot Cancel",
            indicator="orange",
        )
