"""
Purchase Receipt "EFRIS Stock-In Type" changed from codes (101-104) to names
(Import, Local Purchase, ...). Convert stored values so the Select stays valid.
"""

import frappe

CODE_TO_LABEL = {
    "101": "Import",
    "102": "Local Purchase",
    "103": "Manufacture/Assembling",
    "104": "Opening Stock",
}


def execute():
    if not frappe.db.has_column("Purchase Receipt", "efris_stock_in_option"):
        return
    for code, label in CODE_TO_LABEL.items():
        frappe.db.sql(
            "update `tabPurchase Receipt` set efris_stock_in_option = %s where efris_stock_in_option = %s",
            (label, code),
        )
