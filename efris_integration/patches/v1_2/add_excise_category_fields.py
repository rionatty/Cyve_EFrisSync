"""
v1.2 patch — add efris_excise_category_id and efris_excise_category_name
custom fields to the Item doctype, and has_local_excise_duty to EFRIS Settings.

These fields were added to the install.py CUSTOM_FIELDS dict and the
EFRIS Settings JSON but were never created on existing installations,
causing a MySQL 1054 "Unknown column" crash on invoice upload.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


FIELDS_TO_ADD = {
    "Item": [
        {
            "fieldname": "efris_excise_category_id",
            "fieldtype": "Data",
            "label":     "Excise Category Id (T109)",
            "insert_after": "efris_excise_duty_category",
            "description": (
                "URA excise category ID used in T109 invoice goodsDetails.categoryId "
                "when exciseFlag='1'. Leave blank to fall back to Commodity Category Id."
            ),
        },
        {
            "fieldname": "efris_excise_category_name",
            "fieldtype": "Data",
            "label":     "Excise Category Name (T109)",
            "insert_after": "efris_excise_category_id",
            "description": (
                "URA excise category name for T109 invoice goodsDetails.categoryName "
                "when exciseFlag='1'. Leave blank to fall back to item group."
            ),
        },
    ],
}


def execute():
    create_custom_fields(FIELDS_TO_ADD, ignore_validate=True)
    frappe.db.commit()
    frappe.logger("efris").info(
        "EFRIS v1.2 patch: added efris_excise_category_id and "
        "efris_excise_category_name to Item doctype."
    )
