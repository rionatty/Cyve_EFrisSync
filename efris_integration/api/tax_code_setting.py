"""
Tax Code Setting API  —  mirrors TaxCodeSetting.cs

Provides server-side helpers for the EFRIS Tax Mapping doctype.
Allows bulk auto-mapping based on naming conventions.
"""

import frappe


@frappe.whitelist()
def auto_map_tax_accounts() -> dict:
    """
    Auto-detect ERPNext tax accounts and suggest EFRIS codes.
    Rules:
      Account name contains "VAT" or "18%"  → 101 (Standard 18%)
      Account name contains "Zero" or "0%"  → 102 (Zero Rate)
      Account name contains "Exempt"        → 103 (Exempt)
    """
    accounts = frappe.db.get_all(
        "Account",
        filters={"account_type": "Tax", "disabled": 0},
        fields=["name", "account_name"],
    )

    mapped = 0
    for acc in accounts:
        name_upper = (acc.account_name or "").upper()

        if any(k in name_upper for k in ("18%", "VAT", "STANDARD")):
            code = "101"
        elif any(k in name_upper for k in ("ZERO", "0%")):
            code = "102"
        elif "EXEMPT" in name_upper:
            code = "103"
        else:
            continue  # can't guess

        if not frappe.db.exists("EFRIS Tax Mapping", {"erpnext_tax": acc.name}):
            frappe.get_doc({
                "doctype": "EFRIS Tax Mapping",
                "erpnext_tax": acc.name,
                "efris_tax_code": code,
                "description": f"Auto-mapped from account name: {acc.account_name}",
            }).insert(ignore_permissions=True)
            mapped += 1

    frappe.db.commit()
    return {"mapped": mapped}


@frappe.whitelist()
def auto_map_uoms() -> dict:
    """
    Auto-detect ERPNext UOMs and suggest EFRIS UOM codes.
    """
    uom_map = {
        "NOS": "101", "NO": "101", "PIECE": "101", "PCS": "101", "UNIT": "101",
        "KG": "102", "KGS": "102", "KILOGRAM": "102",
        "LTR": "103", "LITRE": "103", "LITER": "103", "L": "103",
        "MTR": "104", "METRE": "104", "METER": "104", "M": "104",
        "BOX": "105", "CARTON": "106",
        "DOZEN": "107", "DZN": "107",
        "TON": "108", "TONNE": "108",
        "GM": "109", "GRAM": "109", "G": "109",
    }

    uoms = frappe.db.get_all("UOM", fields=["name"])
    mapped = 0
    for uom in uoms:
        key = uom.name.upper().strip()
        code = uom_map.get(key)
        if not code:
            continue
        if not frappe.db.exists("EFRIS UOM Mapping", {"erpnext_uom": uom.name}):
            frappe.get_doc({
                "doctype": "EFRIS UOM Mapping",
                "erpnext_uom": uom.name,
                "efris_uom_code": code,
                "efris_uom_name": uom.name,
            }).insert(ignore_permissions=True)
            mapped += 1

    frappe.db.commit()
    return {"mapped": mapped}
