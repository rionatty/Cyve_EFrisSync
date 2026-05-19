"""
v1.3 patch — make URA response fields visible on Stock Entry URA tab.

Previously efris_invoice_id, efris_status and efris_submitted_at were
hidden (hidden=1) on the Stock Entry.  Now that T131 writes the URA
document reference number and submission status back to the document on
success, these fields must be visible so users can:
  • See the URA Reference No returned after a successful T131 upload
  • Know whether the document has already been submitted (duplicate guard)
  • Clear the fields manually if a re-upload is ever needed

Also renames the label of efris_invoice_id from "EFRIS Invoice ID" to
"URA Reference No" to better describe its T131 meaning.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


FIELDS_TO_UPDATE = {
    "Stock Entry": [
        {
            "fieldname":      "efris_invoice_id",
            "fieldtype":      "Data",
            "label":          "URA Reference No",
            "insert_after":   "efris_supplier_tin",
            "read_only":      1,
            "allow_on_submit": 1,
            "hidden":         0,
            "bold":           1,
            "description":    "URA-assigned T131 document number returned after successful EFRIS upload.",
        },
        {
            "fieldname":      "efris_status",
            "fieldtype":      "Select",
            "label":          "EFRIS Status",
            "insert_after":   "efris_invoice_id",
            "read_only":      1,
            "allow_on_submit": 1,
            "hidden":         0,
            "options":        "\nPending\nSubmitted\nFailed",
        },
        {
            "fieldname":      "efris_submitted_at",
            "fieldtype":      "Datetime",
            "label":          "EFRIS Submitted At",
            "insert_after":   "efris_status",
            "read_only":      1,
            "allow_on_submit": 1,
            "hidden":         0,
        },
    ],
}


def execute():
    create_custom_fields(FIELDS_TO_UPDATE, ignore_validate=True)
    frappe.db.commit()
    frappe.logger("efris").info(
        "EFRIS v1.3 patch: Stock Entry URA response fields "
        "(efris_invoice_id, efris_status, efris_submitted_at) made visible."
    )
