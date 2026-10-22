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
    Upload the invoice to EFRIS when EFRIS Settings → "Upload Sales Invoices
    on Submit" is on (SAP B1 uploads on Add). Off by default: the user clicks
    "Upload to EFRIS". Credit notes are always uploaded by hand (they need a
    reason). A failure never blocks the submit; the Upload button stays.
    """
    if doc.is_return:
        return
    # db check (not get_efris_settings): its frappe.throw would pop up a
    # "not configured" message on every submit for non-EFRIS companies
    if not frappe.db.get_value("EFRIS Settings", {"company": doc.company}, "auto_upload_invoices"):
        return

    try:
        from efris_integration.api.invoice import submit_sales_invoice
        submit_sales_invoice(doc.name)
    except Exception as exc:
        frappe.log_error(frappe.get_traceback(), f"EFRIS auto upload {doc.name}")
        frappe.msgprint(
            f"Invoice {doc.name} was saved but not sent to EFRIS: {exc}<br>"
            "Fix the problem, then use <b>Upload to EFRIS</b>.",
            title="EFRIS", indicator="orange",
        )


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
