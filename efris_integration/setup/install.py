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
    "Item": [
        {"fieldname": "efris_section", "fieldtype": "Section Break", "label": "EFRIS / URA", "insert_after": "taxes", "collapsible": 1, "module": "EFRIS Integration"},
        {"fieldname": "efris_goods_category_id", "fieldtype": "Data", "label": "EFRIS Goods Category ID", "description": "e.g. 238 for general goods"},
        {"fieldname": "efris_excise_flag", "fieldtype": "Check", "label": "Excisable Good"},
        {"fieldname": "efris_excise_rate", "fieldtype": "Float", "label": "Excise Rate (%)", "depends_on": "efris_excise_flag"},
        {"fieldname": "efris_col_break", "fieldtype": "Column Break"},
        {"fieldname": "efris_excise_rate_usd", "fieldtype": "Float", "label": "Excise Rate USD/Ton or USD/Unit", "depends_on": "efris_excise_flag"},
        {"fieldname": "efris_weight_unit", "fieldtype": "Float", "label": "Weight Per Unit (kg)", "depends_on": "efris_excise_flag"},
        {"fieldname": "volume_per_unit", "fieldtype": "Float", "label": "Volume Per Unit (Litres)", "depends_on": "efris_excise_flag"},
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
