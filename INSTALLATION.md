# EFRIS Integration for ERPNext v16 — Installation & Configuration Guide

## 1. Install the App

```bash
# From your Frappe bench root
bench get-app /path/to/efris_integration
# OR if hosted on git:
# bench get-app https://github.com/cyvetech/efris_integration

bench --site your-site.localhost install-app efris_integration
bench --site your-site.localhost migrate
bench build --app efris_integration
bench restart
```

---

## 2. Configure EFRIS Settings

Open **EFRIS Settings** (search in the top bar).

| Field | Value | Notes |
|---|---|---|
| **Enable** | ✅ | Master on/off switch |
| **EFRIS API URL** | `https://efrisapi.ura.go.ug/efrisapi/api/method/api` | URA endpoint |
| **API Version** | `1.1.20191201` | |
| **Application ID** | From URA portal | |
| **Device Number** | From URA portal | |
| **TIN** | Company TIN | e.g. `1000XXXXXXX` |
| **Taxpayer ID** | Company Taxpayer ID | |
| **EFRIS Username** | Your EFRIS login | |
| **EFRIS Password** | Your EFRIS password | Stored encrypted |
| **Legal Name** | Registered company name | |
| **Business Name** | Trading name | |

### Industry Flags
| Flag | When to enable |
|---|---|
| **Alcohol Industry** | Excise on alcohol products (60% or USD/unit floor) |
| **Beverage Industry** | Excise based on volume (litres × rate) |
| **Multi-Branch Mode** | If you have multiple branches reporting separately |
| **Issue From Production** | Submit raw material consumption (T115) |
| **Receipt From Production** | Submit finished goods from production (T114) |

### Auto-Submit Flags (recommended defaults)
| Setting | Recommended |
|---|---|
| Auto-Submit Sales Invoice | ✅ |
| Auto-Submit Credit Notes | ✅ |
| Auto-Submit Purchase Receipt | ✅ |
| Auto-Submit Manufacture Entries | ✅ (if manufacturing) |
| Auto-Submit Sales Order | ❌ (optional per URA requirement) |

---

## 3. Initialise the EFRIS Connection (ONE-TIME)

From the **EFRIS Settings** form click:

```
EFRIS Init → 🔑 Full Initialise (T101–T104)
```

This runs the full handshake:
1. **T101** — Verifies connectivity, gets server time
2. **T102** — Downloads server public key + encrypted client private key
3. **T103** — Authenticates your user credentials
4. **T104** — Retrieves the session AES symmetric key

All keys are saved encrypted in the EFRIS Settings record. The hourly
scheduler automatically refreshes the session before it expires.

---

## 4. Map Tax Accounts

Click: **EFRIS Utilities → Auto-Map Tax Accounts**

This scans your Chart of Accounts and auto-assigns EFRIS codes:
- Account with "VAT" / "18%" → `101` (Standard Rate)
- Account with "Zero" / "0%" → `102` (Zero Rate)  
- Account with "Exempt"      → `103` (Exempt)

Review in **EFRIS Tax Mapping** list. Add manual entries for any missed.

---

## 5. Map Units of Measure

Click: **EFRIS Utilities → Auto-Map UOMs**

Reviews your UOM list and maps standard UOMs to EFRIS codes:
- Nos / Piece / PCS → `101`
- KG / Kilogram     → `102`
- Litre / Ltr       → `103`
- Metre / Mtr       → `104`
- Box               → `105`

Review in **EFRIS UOM Mapping** list.

---

## 6. Configure Items (for Excisable Goods)

For items subject to Excise Duty, open the **Item** and scroll to the
**EFRIS / URA** section:

| Field | Description |
|---|---|
| **EFRIS Goods Category ID** | Category code from URA list (e.g. `238`) |
| **Excisable Good** | ✅ to enable excise fields |
| **Excise Rate (%)** | Percentage for value-based excise |
| **Excise Rate USD/Ton or USD/Unit** | Specific excise in USD (alcohol/beverages) |
| **Weight Per Unit (kg)** | For per-tonne calculations |
| **Volume Per Unit (Litres)** | For beverage volume calculations |

---

## 7. Configure Customers

Open any **Customer** → **EFRIS / URA** section:

| Field | Values |
|---|---|
| **Buyer Type** | `B2C` (individual), `B2B` (business), `B2G` (govt), `B2F` (foreign) |
| **NIN / BRN** | National ID or Business Registration Number |
| **Citizenship** | For B2C foreign buyers |
| **Export Customer** | ✅ for zero-rated export sales |

---

## 8. Configure Warehouses (Multi-Branch)

If running **Multi-Branch Mode**, open each **Warehouse** → **EFRIS / URA**:

| Field | Description |
|---|---|
| **EFRIS Branch ID** | Branch identifier from URA portal |
| **EFRIS Branch Name** | Branch display name |
| **EFRIS Branch Code** | Branch code |

---

## 9. Document Workflow

### Sales Invoice → T108
Submits automatically on **Submit** (if Auto-Submit enabled).
Manual button: `EFRIS → Submit to EFRIS`

### Credit Note (Sales Invoice is_return=1) → T110
Must reference an original invoice that was already submitted to EFRIS.
A dialog prompts for the **Reason Code** before submission.

### Sales Order → T109 (optional)
Enable `Submit Sales Orders to EFRIS` in settings if URA requires it.

### Delivery Note → T109
Links to the originating Sales Order's EFRIS Invoice ID.

### Purchase Receipt → T114
SAP B1 equivalent: **GRPO (Goods Receipt PO)**.
Set **EFRIS Stock-In Type** before submitting:
- `101` = Import
- `102` = Local Purchase *(default)*

### Stock Entry – Material Receipt → T114
SAP B1 equivalent: **Goods Receipt (OIGN)**.
Set the **EFRIS Stock-In Type** field on the entry.

### Stock Entry – Material Issue → T115
SAP B1 equivalent: **Goods Issue (OIGE)**.

### Stock Entry – Material Transfer for Manufacture → T115 (Issue From Production)
SAP B1 equivalent: **Issue From Production (OIGE with WO)**.
Auto-submitted with `operationType = 202`.

### Stock Entry – Manufacture → T114 (Receipt From Production)
SAP B1 equivalent: **Receipt From Production**.
Finished goods lines → T114 with `operationType = 103`.
Consumed RM lines → T115 (if `Issue From Production` is enabled in settings).

### Stock Entry – Material Transfer (inter-branch) → T115 + T114
SAP B1 equivalent: **Inventory Transfer**.
Only fires if **Multi-Branch Mode** is ON and warehouses are in different branches.
Issues from source branch (T115) and receives at target branch (T114) atomically.

### Stock Reconciliation → T114 (Opening Stock)
SAP B1 equivalent: **Inventory Posting**.
Submitted with `operationType = 104`.

---

## 10. Exchange Rates

Runs **daily** via the scheduler: `EFRIS Utilities → Sync Exchange Rates (T126)`.
Writes rates into ERPNext's **Currency Exchange** doctype.
Can also be triggered manually from EFRIS Settings.

---

## 11. Bulk Submission (Historical Records)

Navigate to: **Reports → EFRIS Bulk Submission**

1. Select Document Type (Sales Invoice / Purchase Receipt / Stock Entry)
2. Set date range
3. Check boxes next to documents to submit
4. Click **Submit Selected to EFRIS**

---

## 12. Audit Trail

All EFRIS API calls are logged in: **Reports → EFRIS Submission Log**

Filterable by:
- Interface Code (T108, T114, etc.)
- Status (Success / Failed)
- Date range

Full request/response payloads are stored for debugging.

---

## 13. File Structure Summary

```
efris_integration/
├── setup.py                            # pip package definition
├── requirements.txt                    # pycryptodome, requests, qrcode, Pillow
└── efris_integration/
    ├── hooks.py                        # Frappe hooks, event wiring, JS assets
    ├── modules.txt
    ├── excise.py                       # Excise duty engine (Alcohol/Beverage/Standard)
    ├── api/
    │   ├── base.py                     # EFRISClient: encrypt→POST→decrypt
    │   ├── auth.py                     # T101–T104 authentication
    │   ├── invoice.py                  # T108 Sales Invoice, T106 query
    │   ├── credit_note.py              # T110 Credit Note, T111 query
    │   ├── sales_order.py              # T109 Sales Order + Delivery Note
    │   ├── stock.py                    # T114 Receipt, T115 Issue, T119 Return
    │   ├── exchange_rate.py            # T120/T126 Exchange Rates
    │   ├── inventory_transfer.py       # Inter-branch T115+T114
    │   ├── bulk.py                     # Bulk submission
    │   └── tax_code_setting.py        # Auto-map taxes & UOMs
    ├── utils/
    │   ├── encryption.py               # AES-ECB PKCS7, RSA-OAEP, GZip
    │   └── helpers.py                  # Logging, QR, field lookups
    ├── overrides/
    │   ├── sales_invoice.py            # on_submit → T108/T110
    │   ├── sales_order.py              # on_submit → T109
    │   ├── delivery_note.py            # on_submit → T109
    │   ├── stock_entry.py              # on_submit → T114/T115 (all types)
    │   ├── purchase_receipt.py         # on_submit → T114
    │   └── stock_reconciliation.py     # on_submit → T114 opening
    ├── doctype/
    │   ├── efris_settings/             # Single config doc
    │   ├── efris_log/                  # Audit log per API call
    │   ├── efris_tax_mapping/          # ERPNext tax → EFRIS code
    │   └── efris_uom_mapping/          # ERPNext UOM → EFRIS code
    ├── public/js/
    │   ├── efris_utils.js              # Shared: badge, call, confirm, QR, log
    │   ├── sales_invoice.js            # T108/T110 buttons + credit note dialog
    │   ├── sales_order.js              # T109 button + excise recalc
    │   ├── delivery_note.js            # T109 button
    │   ├── purchase_receipt.js         # T114 button + stock-in dialog
    │   ├── stock_entry.js              # T114/T115/T119 buttons per entry type
    │   ├── stock_reconciliation.js     # T114 opening stock button
    │   ├── item.js                     # Excise field show/hide
    │   └── efris_settings.js          # T101–T104 init + auto-map buttons
    ├── report/
    │   ├── efris_bulk_submission/      # Bulk submit portal
    │   └── efris_submission_log/       # Full API audit trail
    ├── setup/
    │   └── install.py                  # Custom fields for all doctypes
    └── patches/
        └── v1_0/install_custom_fields.py
```
