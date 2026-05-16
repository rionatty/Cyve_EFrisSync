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
    "Stock Entry": [
        {"fieldname": "efris_section", "fieldtype": "Section Break", "label": "EFRIS / URA", "insert_after": "remarks", "collapsible": 1, "module": "EFRIS Integration"},
        {"fieldname": "efris_invoice_id", "fieldtype": "Data", "label": "EFRIS Invoice ID", "read_only": 1},
        {"fieldname": "efris_status", "fieldtype": "Select", "label": "EFRIS Status", "options": "\nPending\nSubmitted\nFailed", "read_only": 1},
        {"fieldname": "efris_submitted_at", "fieldtype": "Datetime", "label": "EFRIS Submitted At", "read_only": 1},
        {"fieldname": "efris_col_break", "fieldtype": "Column Break"},
        {"fieldname": "efris_stock_in_option", "fieldtype": "Select", "label": "EFRIS Stock-In Type",
         "options": "\n101\n102\n103\n104\n105",
         "description": "101=Import, 102=Local, 103=Manufacture, 104=Opening, 105=Transfer"},
        {"fieldname": "efris_supplier_name", "fieldtype": "Data", "label": "Supplier Name (EFRIS)"},
        {"fieldname": "efris_supplier_tin", "fieldtype": "Data", "label": "Supplier TIN (EFRIS)"},
        {"fieldname": "efris_reference_no", "fieldtype": "Data", "label": "Reference No (EFRIS)"},
        {"fieldname": "efris_recipient_name", "fieldtype": "Data", "label": "Recipient Name (EFRIS)"},
        {"fieldname": "efris_recipient_tin", "fieldtype": "Data", "label": "Recipient TIN (EFRIS)"},
        {"fieldname": "efris_return_against_invoice_id", "fieldtype": "Data", "label": "Return Against EFRIS Invoice ID"},
        {"fieldname": "efris_return_reason_code", "fieldtype": "Select", "label": "Return Reason Code", "options": "\n101\n102\n103\n104\n105"},
        {"fieldname": "efris_return_reason", "fieldtype": "Data", "label": "Return Reason"},
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

    # ─── Customer ────────────────────────────────────────────────────────
    "Customer": [
        {"fieldname": "efris_section", "fieldtype": "Section Break", "label": "EFRIS / URA", "insert_after": "tax_id", "collapsible": 1, "module": "EFRIS Integration"},
        {"fieldname": "efris_buyer_type", "fieldtype": "Select", "label": "Buyer Type", "options": "B2C\nB2B\nB2F\nB2G", "default": "B2C"},
        {"fieldname": "efris_nin_brn", "fieldtype": "Data", "label": "NIN / BRN"},
        {"fieldname": "efris_citizenship", "fieldtype": "Data", "label": "Citizenship"},
        {"fieldname": "efris_sector", "fieldtype": "Data", "label": "Business Sector"},
        {"fieldname": "efris_export_flag", "fieldtype": "Check", "label": "Export Customer"},
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
