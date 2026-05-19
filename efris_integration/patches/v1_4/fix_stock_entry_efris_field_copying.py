"""
v1.4 patch — prevent Sales Invoice EFRIS fields being copied to Stock Entry.

Root cause
----------
efris_invoice_id, efris_status and efris_submitted_at share the same
fieldname on both Sales Invoice and Stock Entry.  When ERPNext creates a
Stock Entry from a Sales Invoice (delivery, return, etc.) the model mapper
copies every matching custom field — so a new Stock Entry can arrive with
these fields already set to the Sales Invoice's EFRIS values before it has
ever been submitted to T131.

This caused the T131 duplicate-submission guard (which checks
efris_invoice_id / efris_status) to falsely block uploads with:
  "Stock Entry already submitted to URA EFRIS"

Fix applied
-----------
1.  Set no_copy=1 on efris_invoice_id, efris_status, efris_submitted_at
    and efris_ura_remark for the Stock Entry doctype so future document
    creation never copies these values from a parent document.

2.  Clear efris_invoice_id, efris_status and efris_submitted_at on any
    Stock Entry where efris_ura_remark is empty — meaning the entry was
    never actually submitted to T131 but had stale values copied from a
    Sales Invoice.
    Stock Entries that WERE submitted via T131 all have a non-empty
    efris_ura_remark (written by upload_stock_in on success) so they are
    left untouched.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


# ── 1. Apply no_copy to the four affected fields ──────────────────────────────

FIELDS_NO_COPY = {
    "Stock Entry": [
        {
            "fieldname":      "efris_invoice_id",
            "fieldtype":      "Data",
            "label":          "URA Reference No",
            "read_only":      1,
            "no_copy":        1,
            "allow_on_submit": 1,
        },
        {
            "fieldname":      "efris_status",
            "fieldtype":      "Select",
            "label":          "EFRIS Status",
            "read_only":      1,
            "no_copy":        1,
            "allow_on_submit": 1,
            "options":        "\nPending\nSubmitted\nFailed",
        },
        {
            "fieldname":      "efris_submitted_at",
            "fieldtype":      "Datetime",
            "label":          "EFRIS Submitted At",
            "read_only":      1,
            "no_copy":        1,
            "allow_on_submit": 1,
        },
        {
            "fieldname":      "efris_ura_remark",
            "fieldtype":      "Small Text",
            "label":          "URA Remark",
            "read_only":      1,
            "no_copy":        1,
            "allow_on_submit": 1,
        },
    ],
}


def execute():
    # Step 1 — set no_copy on the fields
    create_custom_fields(FIELDS_NO_COPY, ignore_validate=True)

    # Step 2 — clear stale values on Stock Entries that were never submitted
    # to T131 (efris_ura_remark is empty = no T131 call was made).
    # We use direct SQL to keep it fast and avoid permission overhead.
    frappe.db.sql("""
        UPDATE `tabStock Entry`
        SET
            `efris_invoice_id`  = NULL,
            `efris_status`      = NULL,
            `efris_submitted_at`= NULL
        WHERE
            (`efris_invoice_id` IS NOT NULL OR `efris_status` IS NOT NULL)
            AND (`efris_ura_remark` IS NULL OR `efris_ura_remark` = '')
    """)

    frappe.db.commit()

    cleared = frappe.db.sql("""
        SELECT COUNT(*) FROM `tabStock Entry`
        WHERE `efris_ura_remark` IS NOT NULL AND `efris_ura_remark` != ''
          AND `efris_status` = 'Submitted'
    """)[0][0]

    frappe.logger("efris").info(
        f"EFRIS v1.4 patch: no_copy applied to Stock Entry EFRIS fields. "
        f"Stale values cleared from unsubmitted entries. "
        f"{cleared} legitimate T131-submitted entries left intact."
    )
