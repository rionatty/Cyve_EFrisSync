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
    """
    Credit note sent to EFRIS → cancel it there too (T120 void if still pending,
    T114 cancellation if approved). Never blocks the ERPNext cancel; on failure
    the user retries with EFRIS → Cancel in EFRIS.
    Normal invoice → warn: URA has no cancellation, issue a credit note.
    """
    if doc.is_return:
        if doc.get("efris_cn_reference_no") and doc.get("efris_cn_approval") in ("Pending", "Approved"):
            try:
                from efris_integration.api.credit_note import cancel_credit_note_in_efris
                cancel_credit_note_in_efris(doc.name)
            except Exception as exc:
                frappe.log_error(frappe.get_traceback(), f"EFRIS credit note cancel {doc.name}")
                frappe.msgprint(
                    f"Cancelled in ERPNext, but not in EFRIS: {exc}<br>"
                    "Use <b>EFRIS → Cancel in EFRIS</b> to try again.",
                    title="EFRIS", indicator="orange",
                )
        return

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
