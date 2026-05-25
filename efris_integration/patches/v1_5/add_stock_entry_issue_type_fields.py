"""
v1.5 patch — add efris_issue_type and efris_adjust_reason to Stock Entry.

Why
---
Material Issue submissions to T115 previously hard-coded operationType "201"
(Normal Goods Issue).  SAP B1 exposes three adjustment reasons on its EFRIS tab:
  Out of Stock Adjust  → operationType 206
  Error Adjust         → operationType 207
  Others               → operationType 208

This patch adds the two new fields so users can select the correct issue type
(and provide a mandatory reason text for adjustment types) before submitting
a Material Issue or Send to Subcontractor to URA via T115.

Fields added
------------
  efris_issue_type     (Select)     — EFRIS Issue Type
  efris_adjust_reason  (Small Text) — Adjustment Reason (required for 206/207/208)
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


FIELDS = {
    "Stock Entry": [
        {
            "fieldname":    "efris_issue_type",
            "fieldtype":    "Select",
            "label":        "EFRIS Issue Type",
            "insert_after": "efris_return_reason",
            "options":      "\nNormal Issue\nOut of Stock Adjust\nError Adjust\nOthers",
            "default":      "Normal Issue",
            "allow_on_submit": 1,
            "description":  "Maps to T115 operationType: Normal(201), Out of Stock Adjust(206), Error Adjust(207), Others(208).",
            "module":       "EFRIS Integration",
        },
        {
            "fieldname":    "efris_adjust_reason",
            "fieldtype":    "Small Text",
            "label":        "Adjustment Reason",
            "insert_after": "efris_issue_type",
            "allow_on_submit": 1,
            "description":  "Required when EFRIS Issue Type is Out of Stock Adjust, Error Adjust, or Others. Sent as 'remarks' to URA.",
            "module":       "EFRIS Integration",
        },
    ],
}


def execute():
    create_custom_fields(FIELDS, ignore_validate=True)
    frappe.db.commit()
    frappe.logger("efris").info(
        "EFRIS v1.5 patch: added efris_issue_type and efris_adjust_reason "
        "to Stock Entry for T115 adjustment operationType support."
    )
