app_name = "efris_integration"

# Load shared JS utilities on every desk page
app_include_js = "/assets/efris_integration/js/efris_utils.js"

app_title = "EFRIS Integration"
app_publisher = "CyveTech"
app_description = "URA EFRIS Integration for ERPNext v16 – Uganda Revenue Authority"
app_email = "info@cyvetech.com"
app_license = "MIT"

# ─── JavaScript assets bundled per DocType form ────────────────────────────
doctype_js = {
    "Sales Invoice":      "public/js/sales_invoice.js",
    "Sales Order":        "public/js/sales_order.js",
    "Delivery Note":      "public/js/delivery_note.js",
    "Purchase Receipt":   "public/js/purchase_receipt.js",
    "Stock Entry":        "public/js/stock_entry.js",
    "Stock Reconciliation": "public/js/stock_reconciliation.js",
    "Item":               "public/js/item.js",
    "Customer":           "public/js/customer.js",
    "Warehouse":          "public/js/warehouse.js",
    "EFRIS Settings":     "public/js/efris_settings.js",
}

# ─── Document Event Hooks ──────────────────────────────────────────────────
doc_events = {
    "EFRIS Settings": {
        "on_update": "efris_integration.api.t127.invalidate_cache",
    },
    # Excise duty is calculated on the lines before ERPNext's own validation
    # (so totals and taxes include it); see excise.py.
    "Sales Invoice": {
        "before_validate": "efris_integration.excise.before_validate",
        "on_submit": "efris_integration.overrides.sales_invoice.on_submit",
        "on_cancel":  "efris_integration.overrides.sales_invoice.on_cancel",
    },
    # Sales Order / Delivery Note are not sent to URA: the Sales Invoice is the
    # fiscal document (sending them as T109 would fiscalise the sale twice).
    "Sales Order": {
        "before_validate": "efris_integration.excise.before_validate",
    },
    "Delivery Note": {
        "before_validate": "efris_integration.excise.before_validate",
    },
    "Stock Entry": {
        "on_submit": "efris_integration.overrides.stock_entry.on_submit",
    },
    "Purchase Receipt": {
        "on_submit": "efris_integration.overrides.purchase_receipt.on_submit",
    },
    "Stock Reconciliation": {
        "on_submit": "efris_integration.overrides.stock_reconciliation.on_submit",
    },
}

# ─── Scheduled Tasks ───────────────────────────────────────────────────────
scheduler_events = {
    "daily": [
        # Fetches today's URA exchange rates for ALL configured companies.
        "efris_integration.api.exchange_rate.sync_exchange_rates_all_companies",
    ],
    "hourly": [
        # Refreshes the EFRIS session AES key for ALL configured companies
        # whose session key file is older than 7.5 hours.
        "efris_integration.api.auth.refresh_session_if_needed",
    ],
}

# ─── Jinja (print formats) ─────────────────────────────────────────────────
# generate_qr_code_b64(url) renders the EFRIS validation URL as an inline PNG
# for the "EFRIS Tax Invoice" print format (no external QR service needed).
# get_efris_print_data(doc) gives that print format the lines, tax breakdown
# and totals exactly as sent to URA.
jinja = {
    "methods": [
        "efris_integration.utils.helpers.generate_qr_code_b64",
        "efris_integration.api.invoice.get_efris_print_data",
    ],
}

# ─── Install / Migrate ─────────────────────────────────────────────────────
after_install  = "efris_integration.setup.install.after_install"
after_migrate  = "efris_integration.setup.install.after_install"

# ─── Fixtures ──────────────────────────────────────────────────────────────
fixtures = [
    {"dt": "Custom Field",    "filters": [["module", "=", "EFRIS Integration"]]},
    {"dt": "Property Setter", "filters": [["module", "=", "EFRIS Integration"]]},
    {"dt": "Workspace",       "filters": [["name", "=", "EFRIS Integration"]]},
    {"dt": "Print Format",    "filters": [["name", "=", "EFRIS Sales Invoice"]]},
]
