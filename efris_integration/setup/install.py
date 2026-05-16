"""
Custom fields installer.

Adds all EFRIS-related fields to standard ERPNext DocTypes.
Run after_install or after_migrate.

Field mapping rationale (SAP B1 UDF → ERPNext Custom Field):
  OINV/ORIN → Sales Invoice
  ORDR       → Sales Order
  ODLN       → Delivery Note
  OIGN       → Stock Entry (Material Receipt)
  OIGE       → Stock Entry (Material Issue)
  OGRP       → Purchase Receipt
  OITM       → Item
  OCRD (C)   → Customer
  OCRD (S)   → Supplier
  OWHS       → Warehouse
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


CUSTOM_FIELDS = {

    # ─── Sales Invoice (AR Invoice + Credit Note) ─────────────────────────
    "Sales Invoice": [
        {"fieldname": "efris_section", "fieldtype": "Section Break", "label": "EFRIS / URA", "insert_after": "against_income_account", "collapsible": 1, "module": "EFRIS Integration"},
        {"fieldname": "efris_invoice_id", "fieldtype": "Data", "label": "EFRIS Invoice ID", "read_only": 1, "in_list_view": 0},
        {"fieldname": "efris_antifake_code", "fieldtype": "Data", "label": "EFRIS Antifake Code", "read_only": 1},
        {"fieldname": "efris_status", "fieldtype": "Select", "label": "EFRIS Status", "options": "\nPending\nSubmitted\nCredit Note Submitted\nFailed", "read_only": 1},
        {"fieldname": "efris_submitted_at", "fieldtype": "Datetime", "label": "EFRIS Submitted At", "read_only": 1},
        {"fieldname": "efris_qr_code", "fieldtype": "Attach Image", "label": "EFRIS QR Code", "read_only": 1},
        {"fieldname": "efris_col_break", "fieldtype": "Column Break"},
        {"fieldname": "efris_branch_id", "fieldtype": "Data", "label": "EFRIS Branch ID"},
        {"fieldname": "efris_total_excise", "fieldtype": "Currency", "label": "Total Excise Duty", "read_only": 1},
    ],
    "Sales Invoice Item": [
        {"fieldname": "efris_excise_tax", "fieldtype": "Currency", "label": "Excise Duty", "insert_after": "net_amount"},
        {"fieldname": "efris_excise_per_unit", "fieldtype": "Currency", "label": "Excise Per Unit", "insert_after": "efris_excise_tax"},
    ],

    # ─── Sales Order ──────────────────────────────────────────────────────
    "Sales Order": [
        {"fieldname": "efris_section", "fieldtype": "Section Break", "label": "EFRIS / URA", "insert_after": "tc_name", "collapsible": 1, "module": "EFRIS Integration"},
        {"fieldname": "efris_invoice_id", "fieldtype": "Data", "label": "EFRIS Invoice ID", "read_only": 1},
        {"fieldname": "efris_status", "fieldtype": "Select", "label": "EFRIS Status", "options": "\nPending\nSubmitted\nFailed", "read_only": 1},
        {"fieldname": "efris_submitted_at", "fieldtype": "Datetime", "label": "EFRIS Submitted At", "read_only": 1},
        {"fieldname": "efris_total_excise", "fieldtype": "Currency", "label": "Total Excise Duty", "read_only": 1},
    ],
    "Sales Order Item": [
        {"fieldname": "efris_excise_tax", "fieldtype": "Currency", "label": "Excise Duty"},
        {"fieldname": "efris_unit_price_incl_excise", "fieldtype": "Currency", "label": "Unit Price incl. Excise"},
        {"fieldname": "efris_gross_price", "fieldtype": "Currency", "label": "Gross Price"},
    ],

    # ─── Delivery Note ────────────────────────────────────────────────────
    "Delivery Note": [
        {"fieldname": "efris_section", "fieldtype": "Section Break", "label": "EFRIS / URA", "insert_after": "tc_name", "collapsible": 1, "module": "EFRIS Integration"},
        {"fieldname": "efris_invoice_id", "fieldtype": "Data", "label": "EFRIS Invoice ID", "read_only": 1},
        {"fieldname": "efris_antifake_code", "fieldtype": "Data", "label": "EFRIS Antifake Code", "read_only": 1},
        {"fieldname": "efris_status", "fieldtype": "Select", "label": "EFRIS Status", "options": "\nPending\nSubmitted\nFailed", "read_only": 1},
        {"fieldname": "efris_submitted_at", "fieldtype": "Datetime", "label": "EFRIS Submitted At", "read_only": 1},
    ],

    # ─── Purchase Receipt (GRPO) ──────────────────────────────────────────
    "Purchase Receipt": [
        {"fieldname": "efris_section", "fieldtype": "Section Break", "label": "EFRIS / URA", "insert_after": "remarks", "collapsible": 1, "module": "EFRIS Integration"},
        {"fieldname": "efris_invoice_id", "fieldtype": "Data", "label": "EFRIS Invoice ID", "read_only": 1},
        {"fieldname": "efris_status", "fieldtype": "Select", "label": "EFRIS Status", "options": "\nPending\nSubmitted\nFailed", "read_only": 1},
        {"fieldname": "efris_submitted_at", "fieldtype": "Datetime", "label": "EFRIS Submitted At", "read_only": 1},
        {"fieldname": "efris_col_break", "fieldtype": "Column Break"},
        {"fieldname": "efris_stock_in_option", "fieldtype": "Select", "label": "EFRIS Stock-In Type",
         "options": "\n101\n102\n103\n104",
         "description": "101=Import, 102=Local Purchase, 103=Manufacture, 104=Opening Stock",
         "default": "102"},
        {"fieldname": "efris_supplier_tin", "fieldtype": "Data", "label": "Supplier TIN (EFRIS)"},
    ],

    # ─── Stock Entry ──────────────────────────────────────────────────────
    # URA tab: exactly 4 visible fields. Every field has an explicit
    # insert_after so Frappe chains them correctly inside the URA tab.
    # Hidden fields exist in DB for T114/T115/T119 backward compat.
    "Stock Entry": [
        # ── Tab Break — anchored after "remarks" ──────────────────────────────
        {"fieldname": "efris_section",        "fieldtype": "Tab Break",     "label": "URA",
         "insert_after": "remarks",            "module": "EFRIS Integration"},

        # ── Left column ───────────────────────────────────────────────────────
        # allow_on_submit=1 so the user can set these on a submitted Stock Entry
        # before clicking "Upload to EFRIS".
        {"fieldname": "efris_stock_in_type",  "fieldtype": "Select",        "label": "Stock In Type",
         "insert_after": "efris_section",
         "options": "\nImport\nLocal Purchase\nManufacture/Assembling\nOpening Stock",
         "allow_on_submit": 1,
         "module": "EFRIS Integration"},
        {"fieldname": "efris_supplier_name",  "fieldtype": "Data",          "label": "Supplier Name",
         "insert_after": "efris_stock_in_type",
         "allow_on_submit": 1},

        # ── Column Break ──────────────────────────────────────────────────────
        {"fieldname": "efris_col_break",      "fieldtype": "Column Break",
         "insert_after": "efris_supplier_name"},

        # ── Right column ──────────────────────────────────────────────────────
        {"fieldname": "efris_reference_no",   "fieldtype": "Data",          "label": "Reference No",
         "insert_after": "efris_col_break",
         "allow_on_submit": 1,
         "description": "EFRIS invoice number already registered in URA system (optional). Leave blank if no linked invoice."},
        {"fieldname": "efris_ura_remark",     "fieldtype": "Small Text",    "label": "Remark",
         "insert_after": "efris_reference_no",
         "read_only": 1,
         "description": "Populated from URA EFRIS response after T131 submission.",
         "module": "EFRIS Integration"},

        # ── Hidden — kept for T114 / T115 / T119 backward compat ─────────────
        {"fieldname": "efris_supplier_tin",               "fieldtype": "Data",     "label": "Supplier TIN (EFRIS)",
         "insert_after": "efris_ura_remark",               "hidden": 1, "allow_on_submit": 1},
        {"fieldname": "efris_invoice_id",                 "fieldtype": "Data",     "label": "EFRIS Invoice ID",
         "insert_after": "efris_supplier_tin",             "hidden": 1, "read_only": 1},
        {"fieldname": "efris_status",                     "fieldtype": "Select",   "label": "EFRIS Status",
         "insert_after": "efris_invoice_id",               "hidden": 1, "read_only": 1,
         "options": "\nPending\nSubmitted\nFailed"},
        {"fieldname": "efris_submitted_at",               "fieldtype": "Datetime", "label": "EFRIS Submitted At",
         "insert_after": "efris_status",                   "hidden": 1, "read_only": 1},
        {"fieldname": "efris_stock_in_option",            "fieldtype": "Select",   "label": "EFRIS Stock-In Code (Legacy)",
         "insert_after": "efris_submitted_at",             "hidden": 1,
         "options": "\n101\n102\n103\n104\n105"},
        {"fieldname": "efris_recipient_name",             "fieldtype": "Data",     "label": "Recipient Name (EFRIS)",
         "insert_after": "efris_stock_in_option",          "hidden": 1},
        {"fieldname": "efris_recipient_tin",              "fieldtype": "Data",     "label": "Recipient TIN (EFRIS)",
         "insert_after": "efris_recipient_name",           "hidden": 1},
        {"fieldname": "efris_return_against_invoice_id",  "fieldtype": "Data",     "label": "Return Against EFRIS Invoice ID",
         "insert_after": "efris_recipient_tin",            "hidden": 1},
        {"fieldname": "efris_return_reason_code",         "fieldtype": "Select",   "label": "Return Reason Code",
         "insert_after": "efris_return_against_invoice_id","hidden": 1,
         "options": "\n101\n102\n103\n104\n105"},
        {"fieldname": "efris_return_reason",              "fieldtype": "Data",     "label": "Return Reason",
         "insert_after": "efris_return_reason_code",       "hidden": 1},
    ],

    # ─── Stock Reconciliation ─────────────────────────────────────────────
    "Stock Reconciliation": [
        {"fieldname": "efris_section", "fieldtype": "Section Break", "label": "EFRIS / URA", "insert_after": "remarks", "collapsible": 1, "module": "EFRIS Integration"},
        {"fieldname": "efris_invoice_id", "fieldtype": "Data", "label": "EFRIS Invoice ID", "read_only": 1},
        {"fieldname": "efris_status", "fieldtype": "Select", "label": "EFRIS Status", "options": "\nPending\nSubmitted\nFailed", "read_only": 1},
        {"fieldname": "efris_submitted_at", "fieldtype": "Datetime", "label": "EFRIS Submitted At", "read_only": 1},
    ],

    # ─── Item ──────────────────────────────────────────────────────────────
    # Tab Break placed after `item_defaults` (last field of the second "Details"
    # tab in the standard ERPNext Item form) so it appears BEFORE "Inventory".
    # All SAP B1 EFRIS item UDFs are mapped here (ItemMaster.cs / ExciseDuty.cs).
    "Item": [
        # ── Tab ─────────────────────────────────────────────────────────────
        # insert_after="item_defaults" places this tab between the second
        # Details tab and the standard Inventory tab in ERPNext v15/v16.
        {
            "fieldname":    "efris_section",
            "fieldtype":    "Tab Break",
            "label":        "URA Setting",
            "insert_after": "item_defaults",
        },

        # ── Left column ──────────────────────────────────────────────────────
        # Mirror SAP B1 ItemMaster form field order exactly.
        {
            "fieldname":   "efris_upload_type",
            "fieldtype":   "Select",
            "label":       "Upload Type On EFRIS",
            "options":     "\nAdd\nUpdate",
            "description": "Add = register new goods on EFRIS (T130).  Update = modify existing goods (T131).",
        },
        {
            "fieldname": "efris_goods_type",
            "fieldtype": "Select",
            "label":     "Goods Type",
            "options":   "\nGoods\nServices",
            "default":   "Goods",
        },
        {
            "fieldname":   "efris_tax_rate_code",
            "fieldtype":   "Select",
            "label":       "Tax Rate",
            "options":     "\nA\nB\nC\nD\nE",
            "description": "A=Standard(18%), B=Zero-Rated, C=Exempt, D=Special, E=Out of Scope",
        },
        {
            "fieldname": "efris_unit_price",
            "fieldtype": "Float",
            "label":     "Unit Price",
            "default":   "0",
        },
        {
            "fieldname": "efris_reference_no",
            "fieldtype": "Data",
            "label":     "Reference No.",
        },
        {
            "fieldname":   "efris_goods_category_id",
            "fieldtype":   "Data",
            "label":       "Commodity Category Id",
            "description": "e.g. 238 for general goods",
        },
        {
            "fieldname": "efris_stock_prewarning",
            "fieldtype": "Float",
            "label":     "Stock Prewarning",
            "default":   "0",
        },
        {
            "fieldname": "efris_have_piece_unit",
            "fieldtype": "Select",
            "label":     "Have Piece Unit",
            "options":   "\nYes\nNo",
        },
        {
            "fieldname": "efris_have_excise_tax",
            "fieldtype": "Select",
            "label":     "Have Excise Tax",
            "options":   "\nYes\nNo",
        },
        # Legacy hidden checkbox — kept for backward-compat with invoice submission logic
        {
            "fieldname": "efris_excise_flag",
            "fieldtype": "Check",
            "label":     "Excisable Good",
            "hidden":    1,
        },

        # ── Column Break ─────────────────────────────────────────────────────
        {"fieldname": "efris_col_break", "fieldtype": "Column Break"},

        # ── Right column ─────────────────────────────────────────────────────
        {
            "fieldname": "efris_excise_duty_category",
            "fieldtype": "Data",
            "label":     "Excise Duty Category",
        },
        {
            "fieldname": "efris_piece_uom",
            "fieldtype": "Data",
            "label":     "Piece UOM",
        },
        {
            "fieldname": "efris_piece_unit_price",
            "fieldtype": "Float",
            "label":     "Piece Unit Price",
            "default":   "0",
        },
        {
            "fieldname": "efris_excise_weight",
            "fieldtype": "Data",
            "label":     "Excise Weight",
        },
        {
            "fieldname": "efris_piece_scaled_value",
            "fieldtype": "Float",
            "label":     "Piece Scaled Value",
            "default":   "0",
        },
        {
            "fieldname": "efris_excise_rate",
            "fieldtype": "Float",
            "label":     "Excise Rate (%)",
            "default":   "0",
        },
        {
            "fieldname": "efris_excise_rate_usd",
            "fieldtype": "Float",
            "label":     "Excise Rate USD/Tonne",
            "default":   "0",
        },
        {
            "fieldname": "efris_weight_unit",
            "fieldtype": "Float",
            "label":     "Weight Per Unit (kg)",
        },
        {
            "fieldname": "volume_per_unit",
            "fieldtype": "Float",
            "label":     "Volume Per Unit (Litres)",
        },
        {
            "fieldname": "efris_item_remark",
            "fieldtype": "Small Text",
            "label":     "Remark",
        },
        {
            "fieldname": "efris_stick",
            "fieldtype": "Check",
            "label":     "Stick",
        },
    ],

    # ─── Customer ─────────────────────────────────────────────────────────
    # EFRIS tab — mirrors SAP B1 Business Partner → EFRIS tab:
    #   Export | Business Type | Invoice Name | Import | Deemed VAT
    # Old fields kept hidden so invoice.py (_buyer_details) still works.
    "Customer": [
        # ── Tab Break ─────────────────────────────────────────────────────
        {"fieldname": "efris_tab",          "fieldtype": "Tab Break", "label": "EFRIS",
         "insert_after": "tax_id",          "module": "EFRIS Integration"},

        # ── Visible fields (SAP B1 EFRIS tab layout) ──────────────────────
        {"fieldname": "efris_export",       "fieldtype": "Select",    "label": "Export",
         "insert_after": "efris_tab",
         "options": "\nY - Yes\nN - No",    "module": "EFRIS Integration"},

        {"fieldname": "efris_business_type","fieldtype": "Select",    "label": "Business Type",
         "insert_after": "efris_export",
         "options": "\nB2F\nB2G\nB2B\nB2C"},

        {"fieldname": "efris_invoice_name", "fieldtype": "Data",      "label": "Invoice Name",
         "insert_after": "efris_business_type"},

        {"fieldname": "efris_import",       "fieldtype": "Select",    "label": "Import",
         "insert_after": "efris_invoice_name",
         "options": "\nY - Yes\nN - No",    "module": "EFRIS Integration"},

        {"fieldname": "efris_deemed_vat",   "fieldtype": "Select",    "label": "Deemed VAT",
         "insert_after": "efris_import",
         "options": "\nY - Yes\nN - No",    "module": "EFRIS Integration"},

        # ── Hidden — backward compat with invoice.py _buyer_details() ─────
        {"fieldname": "efris_section",      "fieldtype": "Section Break", "label": "EFRIS (Legacy)",
         "insert_after": "efris_deemed_vat","hidden": 1},
        {"fieldname": "efris_buyer_type",   "fieldtype": "Select",    "label": "Buyer Type",
         "insert_after": "efris_section",
         "options": "\nB2C\nB2B\nB2F\nB2G","default": "B2C", "hidden": 1},
        {"fieldname": "efris_nin_brn",      "fieldtype": "Data",      "label": "NIN / BRN",
         "insert_after": "efris_buyer_type","hidden": 1},
        {"fieldname": "efris_citizenship",  "fieldtype": "Data",      "label": "Citizenship",
         "insert_after": "efris_nin_brn",   "hidden": 1},
        {"fieldname": "efris_sector",       "fieldtype": "Data",      "label": "Business Sector",
         "insert_after": "efris_citizenship","hidden": 1},
        {"fieldname": "efris_export_flag",  "fieldtype": "Check",     "label": "Export Customer",
         "insert_after": "efris_sector",    "hidden": 1},
    ],

    # ─── Supplier ────────────────────────────────────────────────────────
    "Supplier": [
        {"fieldname": "efris_section", "fieldtype": "Section Break", "label": "EFRIS / URA", "insert_after": "tax_id", "collapsible": 1, "module": "EFRIS Integration"},
        {"fieldname": "efris_nin_brn", "fieldtype": "Data", "label": "NIN / BRN"},
    ],

    # ─── Warehouse ────────────────────────────────────────────────────────
    "Warehouse": [
        {"fieldname": "efris_section", "fieldtype": "Section Break", "label": "EFRIS / URA", "insert_after": "company", "collapsible": 1, "module": "EFRIS Integration"},
        {"fieldname": "efris_branch_id", "fieldtype": "Data", "label": "EFRIS Branch ID"},
        {"fieldname": "efris_branch_name", "fieldtype": "Data", "label": "EFRIS Branch Name"},
        {"fieldname": "efris_branch_code", "fieldtype": "Data", "label": "EFRIS Branch Code"},
    ],
}


def after_install():
    create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)
    frappe.db.commit()
    frappe.msgprint("EFRIS Integration: Custom fields installed successfully.")
