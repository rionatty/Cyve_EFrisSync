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

import copy

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


# Stock Entry URA-tab fields shown only for the entry types that use them.
SE_RECEIPT = "eval:doc.stock_entry_type=='Material Receipt'"
SE_ISSUE = "eval:in_list(['Material Issue','Send to Subcontractor'], doc.stock_entry_type)"
SE_TRANSFER = "eval:doc.stock_entry_type=='Material Transfer'"
SE_ISSUE_OR_TRANSFER = (
    "eval:in_list(['Material Issue','Send to Subcontractor','Material Transfer'], doc.stock_entry_type)"
)

CUSTOM_FIELDS = {

    # ─── Sales Invoice (AR Invoice + Credit Note) ─────────────────────────
    # Mirrors SAP B1: AR Invoice → EFRIS tab
    #   QR Code | QR Path | Verification Code | FDN Number | Invoice ID | Remark
    "Sales Invoice": [
        # ── EFRIS Tab ─────────────────────────────────────────────────────
        {"fieldname": "efris_tab",          "fieldtype": "Tab Break",   "label": "EFRIS",
         "insert_after": "against_income_account", "module": "EFRIS Integration"},

        # ── Left column ───────────────────────────────────────────────────
        {"fieldname": "efris_col_left",     "fieldtype": "Section Break","label": "",
         "insert_after": "efris_tab",       "module": "EFRIS Integration"},

        {"fieldname": "efris_qr_code",      "fieldtype": "Small Text",   "label": "QR Code",
         "insert_after": "efris_col_left",  "read_only": 1,
         "module": "EFRIS Integration"},

        {"fieldname": "efris_qr_path",      "fieldtype": "Small Text",   "label": "QR Validation URL",
         "insert_after": "efris_qr_code",   "read_only": 1,
         "module": "EFRIS Integration"},

        {"fieldname": "efris_antifake_code","fieldtype": "Data",         "label": "Verification Code",
         "insert_after": "efris_qr_path",   "read_only": 1,
         "module": "EFRIS Integration"},

        {"fieldname": "efris_fdn_number",   "fieldtype": "Data",         "label": "FDN Number",
         "insert_after": "efris_antifake_code", "read_only": 1,
         "module": "EFRIS Integration"},

        {"fieldname": "efris_invoice_id",   "fieldtype": "Data",         "label": "Invoice ID",
         "insert_after": "efris_fdn_number","read_only": 1,
         "module": "EFRIS Integration"},

        {"fieldname": "efris_remark",       "fieldtype": "Small Text",   "label": "Remark",
         "insert_after": "efris_invoice_id","read_only": 1,
         "module": "EFRIS Integration"},

        # ── Hidden legacy / internal fields ───────────────────────────────
        {"fieldname": "efris_status",       "fieldtype": "Select",       "label": "EFRIS Status",
         "insert_after": "efris_remark",
         "options": "\nPending\nSubmitted\nCredit Note Submitted\nFailed",
         "read_only": 1, "hidden": 1,      "module": "EFRIS Integration"},

        {"fieldname": "efris_submitted_at", "fieldtype": "Datetime",     "label": "EFRIS Submitted At",
         "insert_after": "efris_status",    "read_only": 1, "hidden": 1,
         "module": "EFRIS Integration"},

        {"fieldname": "efris_branch_id",    "fieldtype": "Data",         "label": "EFRIS Branch ID",
         "insert_after": "efris_submitted_at", "hidden": 1,
         "module": "EFRIS Integration"},

        {"fieldname": "efris_total_excise", "fieldtype": "Currency",     "label": "Total Excise Duty",
         "insert_after": "efris_branch_id", "read_only": 1, "hidden": 1,
         "module": "EFRIS Integration"},
    ],
    "Sales Invoice Item": [
        {"fieldname": "efris_excise_tax", "fieldtype": "Currency", "label": "Excise Duty", "insert_after": "net_amount"},
        {"fieldname": "efris_excise_per_unit", "fieldtype": "Currency", "label": "Excise Per Unit", "insert_after": "efris_excise_tax"},
    ],

    # ─── Sales Order ──────────────────────────────────────────────────────
    "Sales Order": [
        {"fieldname": "efris_section", "fieldtype": "Tab Break", "label": "EFRIS"},
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
        {"fieldname": "efris_section", "fieldtype": "Tab Break", "label": "EFRIS"},
        {"fieldname": "efris_invoice_id", "fieldtype": "Data", "label": "EFRIS Invoice ID", "read_only": 1},
        {"fieldname": "efris_antifake_code", "fieldtype": "Data", "label": "EFRIS Antifake Code", "read_only": 1},
        {"fieldname": "efris_status", "fieldtype": "Select", "label": "EFRIS Status", "options": "\nPending\nSubmitted\nFailed", "read_only": 1},
        {"fieldname": "efris_submitted_at", "fieldtype": "Datetime", "label": "EFRIS Submitted At", "read_only": 1},
    ],

    # ─── Purchase Receipt (GRPO) ──────────────────────────────────────────
    "Purchase Receipt": [
        {"fieldname": "efris_section", "fieldtype": "Tab Break", "label": "EFRIS"},
        {"fieldname": "efris_stock_in_option", "fieldtype": "Select", "label": "EFRIS Stock-In Type",
         "options": "\nImport\nLocal Purchase\nManufacture/Assembling\nOpening Stock",
         "default": "Local Purchase", "allow_on_submit": 1, "depends_on": "eval:!doc.is_return"},
        {"fieldname": "efris_supplier_tin", "fieldtype": "Data", "label": "Supplier TIN (EFRIS)",
         "allow_on_submit": 1, "depends_on": "eval:!doc.is_return"},
        {"fieldname": "efris_col_break", "fieldtype": "Column Break"},
        {"fieldname": "efris_invoice_id", "fieldtype": "Data", "label": "URA Reference No", "read_only": 1, "no_copy": 1},
        {"fieldname": "efris_status", "fieldtype": "Select", "label": "EFRIS Status", "options": "\nPending\nSubmitted\nFailed", "read_only": 1, "no_copy": 1},
        {"fieldname": "efris_submitted_at", "fieldtype": "Datetime", "label": "EFRIS Submitted At", "read_only": 1, "no_copy": 1},
        {"fieldname": "efris_ura_remark", "fieldtype": "Small Text", "label": "URA Remark", "read_only": 1, "no_copy": 1},
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
         "allow_on_submit": 1, "depends_on": SE_RECEIPT,
         "module": "EFRIS Integration"},
        {"fieldname": "efris_supplier_name",  "fieldtype": "Data",          "label": "Supplier Name",
         "insert_after": "efris_stock_in_type",
         "allow_on_submit": 1, "depends_on": SE_RECEIPT},
        {"fieldname": "efris_supplier_tin",   "fieldtype": "Data",          "label": "Supplier TIN",
         "allow_on_submit": 1, "depends_on": SE_RECEIPT, "hidden": 0},   # was hidden before
        # Stock out (Material Issue / Send to Subcontractor)
        {"fieldname": "efris_adjust_type",    "fieldtype": "Select",        "label": "Adjust Type",
         "options": "\nExpired Goods\nDamaged Goods\nPersonal Uses\nOthers\nRaw Materials",
         "allow_on_submit": 1, "depends_on": SE_ISSUE},
        # Branch transfer (Material Transfer between URA branches)
        {"fieldname": "efris_transfer_type",  "fieldtype": "Select",        "label": "Transfer Type",
         "options": "\nOut of Stock Adjust\nError Adjust\nOthers",
         "allow_on_submit": 1, "depends_on": SE_TRANSFER},
        {"fieldname": "efris_vehicle_no",     "fieldtype": "Data",          "label": "Vehicle No",
         "allow_on_submit": 1, "depends_on": SE_TRANSFER},
        {"fieldname": "efris_adjust_reason",  "fieldtype": "Small Text",    "label": "Reason",
         "allow_on_submit": 1, "depends_on": SE_ISSUE_OR_TRANSFER},

        # ── Column Break ──────────────────────────────────────────────────────
        {"fieldname": "efris_col_break",      "fieldtype": "Column Break",
         "insert_after": "efris_supplier_name"},

        # ── Right column ──────────────────────────────────────────────────────
        {"fieldname": "efris_reference_no",   "fieldtype": "Data",          "label": "Reference No",
         "insert_after": "efris_col_break",
         "allow_on_submit": 1},
        {"fieldname": "efris_ura_remark",     "fieldtype": "Small Text",    "label": "URA Remark",
         "insert_after": "efris_reference_no",
         "read_only": 1,
         "no_copy":  1,   # T131-specific — must not be copied from parent documents
         "allow_on_submit": 1,
         "module": "EFRIS Integration"},

        # ── URA response fields (visible — written on T131 success) ─────────
        {
            "fieldname":      "efris_invoice_id",
            "fieldtype":      "Data",
            "label":          "URA Reference No",
            "insert_after":   "efris_ura_remark",
            "read_only":      1,
            "no_copy":        1,   # prevent Sales Invoice efris_invoice_id being copied here
            "allow_on_submit": 1,
            "bold":           1,
        },
        {
            "fieldname":      "efris_status",
            "fieldtype":      "Select",
            "label":          "EFRIS Status",
            "insert_after":   "efris_invoice_id",
            "read_only":      1,
            "no_copy":        1,   # prevent Sales Invoice efris_status being copied here
            "allow_on_submit": 1,
            "options":        "\nPending\nSubmitted\nFailed",
        },
        {
            "fieldname":      "efris_submitted_at",
            "fieldtype":      "Datetime",
            "label":          "EFRIS Submitted At",
            "insert_after":   "efris_status",
            "read_only":      1,
            "no_copy":        1,
            "allow_on_submit": 1,
        },
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
        {"fieldname": "efris_section", "fieldtype": "Tab Break", "label": "EFRIS"},
        {"fieldname": "efris_invoice_id", "fieldtype": "Data", "label": "URA Reference No", "read_only": 1, "no_copy": 1},
        {"fieldname": "efris_status", "fieldtype": "Select", "label": "EFRIS Status", "options": "\nPending\nSubmitted\nFailed", "read_only": 1, "no_copy": 1},
        {"fieldname": "efris_submitted_at", "fieldtype": "Datetime", "label": "EFRIS Submitted At", "read_only": 1, "no_copy": 1},
        {"fieldname": "efris_ura_remark", "fieldtype": "Small Text", "label": "URA Remark", "read_only": 1, "no_copy": 1},
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
            "fieldname": "efris_excise_category_id",
            "fieldtype": "Data",
            "label":     "Excise Category Id",
        },
        {
            "fieldname": "efris_excise_category_name",
            "fieldtype": "Data",
            "label":     "Excise Category Name",
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
    # "Verify TIN" is a Button field placed right after tax_id so it renders
    # inline on the form (visible without opening any menu).
    # Old fields kept hidden so invoice.py (_buyer_details) still works.
    "Customer": [
        # ── Tab Break ─────────────────────────────────────────────────────
        {"fieldname": "efris_tab",          "fieldtype": "Tab Break", "label": "EFRIS",
         "module": "EFRIS Integration"},

        # ── Verify TIN button — kept hidden; toolbar button used instead ───
        # (Button-type custom field; JS adds it to the form toolbar via
        #  frm.add_custom_button so it appears next to Save / Actions.)
        {"fieldname": "efris_verify_tin_btn", "fieldtype": "Button", "label": "Verify TIN",
         "module": "EFRIS Integration", "hidden": 1},

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
        {"fieldname": "efris_section", "fieldtype": "Tab Break", "label": "EFRIS"},
        {"fieldname": "efris_nin_brn", "fieldtype": "Data", "label": "NIN / BRN"},
    ],

    # ─── Warehouse ────────────────────────────────────────────────────────
    # Mirrors SAP B1: Warehouse master → URA tab
    #   URA Warehouse  — Link to the URA warehouse code (maps this ERPNext
    #                    warehouse to the corresponding URA warehouse)
    #   URA Branch Id  — URA branch identifier for this warehouse
    #   URA Branch Name — Human-readable URA branch name
    "Warehouse": [
        {"fieldname": "efris_section",      "fieldtype": "Tab Break",     "label": "URA",
         "module": "EFRIS Integration"},

        {"fieldname": "efris_ura_warehouse","fieldtype": "Select",         "label": "URA Warehouse",
         "insert_after": "efris_section",
         "options": "\nYes\nNo",
         "module": "EFRIS Integration"},

        {"fieldname": "efris_branch_id",    "fieldtype": "Data",          "label": "URA Branch Id",
         "insert_after": "efris_ura_warehouse",
         "module": "EFRIS Integration"},

        {"fieldname": "efris_branch_name",  "fieldtype": "Data",          "label": "URA Branch Name",
         "insert_after": "efris_branch_id",
         "module": "EFRIS Integration"},

        # Button field — renders inline inside the URA section
        {"fieldname": "efris_get_branch_btn", "fieldtype": "Button", "label": "Get Branch ID from URA",
         "insert_after": "efris_branch_name",
         "module": "EFRIS Integration"},
    ],
}


# Every header doctype gets its EFRIS fields in one tab. The list for each
# doctype must start with its Tab Break; _build_custom_fields() then chains
# insert_after so the fields stay in list order inside that tab.
# None = put the tab after the doctype's last standard field (end of the form),
# so it never swallows standard fields that follow the anchor.
EFRIS_TAB_ANCHORS = {
    "Sales Invoice":        None,
    "Sales Order":          None,
    "Delivery Note":        None,
    "Purchase Receipt":     None,
    "Stock Entry":          None,
    "Stock Reconciliation": None,
    "Customer":             None,
    "Supplier":             None,
    "Warehouse":            None,
    # Item keeps its tab between the second Details tab and Inventory.
    "Item":                 "item_defaults",
}


def _last_standard_field(doctype: str) -> str:
    return frappe.get_all(
        "DocField",
        filters={"parent": doctype, "parenttype": "DocType"},
        pluck="fieldname",
        order_by="idx desc",
        limit=1,
    )[0]


def _build_custom_fields() -> dict:
    fields = copy.deepcopy(CUSTOM_FIELDS)
    # No help text under EFRIS fields. Set it explicitly so migrate also
    # clears descriptions saved by earlier versions on existing sites.
    for field_list in fields.values():
        for df in field_list:
            df["description"] = ""
    for doctype, anchor in EFRIS_TAB_ANCHORS.items():
        prev = anchor or _last_standard_field(doctype)
        for df in fields[doctype]:
            df["insert_after"] = prev
            df.setdefault("module", "EFRIS Integration")
            prev = df["fieldname"]
    return fields


def after_install():
    _remove_obsolete_fields()
    create_custom_fields(_build_custom_fields(), ignore_validate=True)
    frappe.db.commit()
    frappe.msgprint("EFRIS Integration: Custom fields installed successfully.")


def _remove_obsolete_fields():
    """Drop custom fields that were renamed or removed across versions."""
    obsolete = [
        # Warehouse — efris_branch_code removed; replaced by efris_ura_warehouse
        ("Warehouse", "efris_branch_code"),
        # Sales Invoice — old section/col breaks replaced by Tab Break
        ("Sales Invoice", "efris_section"),
        ("Sales Invoice", "efris_col_break"),
    ]
    for dt, fieldname in obsolete:
        cf_name = f"{dt}-{fieldname}"
        if frappe.db.exists("Custom Field", cf_name):
            frappe.delete_doc("Custom Field", cf_name, ignore_missing=True)
            frappe.logger("efris").info(f"EFRIS: removed obsolete custom field {cf_name}")
