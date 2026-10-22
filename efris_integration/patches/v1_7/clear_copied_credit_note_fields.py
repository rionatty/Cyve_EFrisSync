"""
"Create > Return / Credit Note" used to copy the original invoice's URA fields
(Invoice ID, FDN, verification code, QR, status) onto the credit note, which
hid its "Upload Credit Note to EFRIS" button. Those fields are now no_copy;
clear the values already copied onto existing credit notes.

A credit note is only touched when its Invoice ID equals its original
invoice's Invoice ID (i.e. it was copied, never sent itself).
"""

import frappe

FIELDS = (
    "efris_invoice_id", "efris_fdn_number", "efris_antifake_code",
    "efris_qr_code", "efris_qr_path", "efris_remark", "efris_status", "efris_submitted_at",
)


def execute():
    if not frappe.db.has_column("Sales Invoice", "efris_invoice_id"):
        return
    copied = frappe.db.sql(
        """
        select cn.name
        from `tabSales Invoice` cn
        join `tabSales Invoice` ori on ori.name = cn.return_against
        where cn.is_return = 1
          and ifnull(cn.efris_invoice_id, '') != ''
          and cn.efris_invoice_id = ori.efris_invoice_id
        """,
        pluck=True,
    )
    for name in copied:
        frappe.db.set_value("Sales Invoice", name, {f: None for f in FIELDS}, update_modified=False)
