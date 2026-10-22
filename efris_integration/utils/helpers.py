"""
General-purpose helpers for EFRIS integration.
All lookups are per-company — pass the company name wherever settings are needed.
"""

import io
import uuid
from datetime import datetime

import frappe
import qrcode
from PIL import Image


# ── Time / UUID ───────────────────────────────────────────────────────────────

def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def generate_data_exchange_id() -> str:
    return str(uuid.uuid4()).replace("-", "")[:32]


# ── Global info envelope (outer EFRIS packet) ─────────────────────────────────

def make_global_info(interface_code: str, settings) -> dict:
    """
    Build the globalInfo section of every EFRIS request.

    Field order and names mirror the SAP B1 GlobalInfo class exactly:
        public string AppID          → appId
        public string BRN            → brn
        public string DataExchangeId → dataExchangeId
        public string DeviceMAC      → deviceMAC
        public string DeviceNo       → deviceNo
        public ExtendField ExtendField → extendField
        public string Longitude      → longitude
        public string Latitude       → latitude
        public string InterfaceCode  → interfaceCode
        public string RequestCode    → requestCode
        public string RequestTime    → requestTime
        public string ResponseCode   → responseCode
        public string TaxpayerID     → taxpayerID
        public string TIN            → tin
        public string UserName       → userName
        public string Version        → version

    Note: agentType is NOT in the SAP B1 GlobalInfo class and has been removed.
    """
    return {
        "appId":          settings.app_id or "AP04",
        "brn":            settings.branch_id or "",
        "dataExchangeId": generate_data_exchange_id(),
        "deviceMAC":      "FFFFFFFFFFFF",
        "deviceNo":       settings.device_no or "",
        "extendField":    {
            "responseDateFormat":     "dd/MM/yyyy",
            "responseTimeFormat":     "dd/MM/yyyy HH:mm:ss",
        },
        "longitude":      "0",
        "latitude":       "0",
        "interfaceCode":  interface_code,
        "requestCode":    "TP",
        "requestTime":    now_str(),
        "responseCode":   "TA",
        "taxpayerID":     settings.taxpayer_id or "",
        "tin":            settings.tin or "",
        "userName":       settings.efris_username or "admin",
        "version":        settings.api_version or "1.1.20191201",
    }


# ── EFRIS Settings lookup (per-company) ───────────────────────────────────────

def get_efris_settings(company: str):
    """
    Return the EFRIS Settings document for the given company.
    Raises a clear, user-friendly error if not configured.
    """
    from efris_integration.efris_integration.doctype.efris_settings.efris_settings import get_settings
    return get_settings(company)


# ── UOM mapping (per-company) ─────────────────────────────────────────────────

def get_mapped_ura_uom(erpnext_uom: str, company: str) -> str:
    """
    Return the URA UOM mapped to an ERPNext UOM in the company's EFRIS UOM
    Mapping table (one record per company, named after it), or "" if none.
    Matches the row's Code first, then its Name.
    """
    for field in ("code", "uom_name"):
        ura_uom = frappe.db.get_value(
            "EFRIS UOM Mapping Item",
            {"parenttype": "EFRIS UOM Mapping", "parent": company, field: erpnext_uom},
            "ura_uom",
        )
        if ura_uom:
            return ura_uom
    return ""


# Used when a UOM has no (valid) row in the company's EFRIS UOM Mapping.
DEFAULT_URA_UOMS = {
    "Nos": "PP", "No": "PP", "Pcs": "PP", "Pieces": "PP", "Unit": "PP", "Units": "PP",
    "Kg": "KGM", "Kgs": "KGM", "Kilogram": "KGM", "Kilograms": "KGM",
    "g": "GRM", "Gram": "GRM", "Grams": "GRM",
    "Ltr": "LTR", "Ltrs": "LTR", "Litre": "LTR", "Litres": "LTR", "Liter": "LTR",
    "Ml": "MLT", "mL": "MLT", "Millilitre": "MLT",
    "Mtr": "MTR", "Meter": "MTR", "Metre": "MTR",
    "Cm": "CMT", "Centimeter": "CMT",
    "Box": "BX", "Boxes": "BX",
    "Dozen": "DZ", "Dz": "DZ",
    "Bundle": "BE",
    "Bag": "BG", "Bags": "BG",
    "Bottle": "BO", "Bottles": "BO",
    "Carton": "CT", "Cartons": "CT",
    "Roll": "RL", "Rolls": "RL",
    "Pair": "PR", "Pairs": "PR",
    "Set": "SET",
    "Tonne": "TNE", "Ton": "TNE", "Tons": "TNE",
}


def map_uom(erpnext_uom: str, company: str) -> str:
    """
    Map an ERPNext UOM to its URA UOM code (max 3 chars) for the given company.

    Used for item registration, invoices and stock documents alike, so a line's
    unitOfMeasure always matches the unit the item was registered with.
    Order: company's EFRIS UOM Mapping → DEFAULT_URA_UOMS → "PP" (Piece).
    Never returns the raw ERPNext name — URA rejects it (error 1208).
    """
    if not erpnext_uom:
        return "PP"

    ura_uom = str(get_mapped_ura_uom(erpnext_uom, company) or "").strip()
    if ura_uom:
        if len(ura_uom) <= 3:
            return ura_uom
        frappe.logger("efris").warning(
            f"EFRIS UOM Mapping '{erpnext_uom}' → '{ura_uom}' is {len(ura_uom)} chars "
            f"(max 3). Check EFRIS UOM Mapping for company '{company}'."
        )

    return DEFAULT_URA_UOMS.get(erpnext_uom, "PP")


# ── Tax code mapping (per-company) ────────────────────────────────────────────

def map_tax_code(erpnext_tax: str, company: str) -> str:
    """
    Map an ERPNext tax code/account to its URA Tax Code for the given company.
    Returns the URA tax code (e.g. "18", "0", "-").
    Falls back to "18" (standard VAT) if not mapped.
    """
    row = frappe.db.get_value(
        "EFRIS Tax Mapping Item",
        {"parenttype": "EFRIS Tax Mapping", "parent": company, "erpnext_tax_code": erpnext_tax},
        "ura_tax_code",
    )
    return row or "18"


# ── Item EFRIS fields ─────────────────────────────────────────────────────────

def get_item_efris_fields(item_code: str) -> dict:
    return frappe.db.get_value(
        "Item",
        item_code,
        [
            "efris_goods_category_id",
            "efris_excise_flag",
            "efris_excise_rate",
            "efris_excise_rate_usd",
            "efris_excise_category_id",    # exciseDutyCode for categoryId (T109 API)
            "efris_excise_category_name",  # excise duty name for categoryName
            "efris_weight_unit",
            "weight_per_unit",
            "weight_uom",
        ],
        as_dict=True,
    ) or {}


# ── Customer EFRIS fields ─────────────────────────────────────────────────────

def get_customer_efris_fields(customer: str) -> dict:
    return frappe.db.get_value(
        "Customer",
        customer,
        [
            "tax_id",
            "efris_business_type",   # new EFRIS tab field (B2F/B2G/B2B/B2C)
            "efris_buyer_type",      # legacy fallback
            "efris_nin_brn",
            "efris_citizenship",
            "efris_sector",
            "efris_export",          # "Y - Yes" / "N - No" → drives invoiceIndustryCode
            "efris_export_flag",     # legacy boolean export flag
            "efris_deemed_vat",      # "Y - Yes" → deemed VAT invoice (T137 project)
            "efris_invoice_name",    # name URA holds for this TIN (set by Verify TIN)
            "customer_name",
            "mobile_no",
            "email_id",
        ],
        as_dict=True,
    ) or {}


# ── Supplier EFRIS fields ─────────────────────────────────────────────────────

def get_supplier_efris_fields(supplier: str) -> dict:
    return frappe.db.get_value(
        "Supplier",
        supplier,
        ["tax_id", "efris_nin_brn", "mobile_no", "email_id"],
        as_dict=True,
    ) or {}


# ── Multi-branch: resolve URA branch from warehouse ──────────────────────────

def resolve_branch_for_doc(doc, settings) -> tuple:
    """
    When "Enable Multi-Branch" is checked in EFRIS Settings, read the URA
    Branch Id and Branch Name from the primary warehouse on the document and
    overwrite settings.branch_id / settings.branch_name in-place so that both
    globalInfo.brn and sellerDetails.branchId / branchName use the correct branch.

    Warehouse priority:
      1. doc.set_warehouse  (global warehouse set on the document header)
      2. First non-empty line.warehouse  (first item line)
      3. First non-empty line.s_warehouse  (Stock Entry source warehouse)
      4. First non-empty line.t_warehouse  (Stock Entry target warehouse)

    Raises a user-friendly error if multi-branch is enabled but the resolved
    warehouse has no URA Branch Id configured.

    Returns:
        (branch_id, branch_name) — the resolved values (same as settings.branch_id
        after the call) so callers can sync to a second client.settings copy.
    """
    if not getattr(settings, "multi_branch", False):
        # Single-branch mode — use settings as-is, nothing to do
        return (settings.branch_id or "", settings.branch_name or "")

    # ── Find the primary warehouse ────────────────────────────────────────
    warehouse = (getattr(doc, "set_warehouse", "") or "").strip()

    if not warehouse:
        for line in (getattr(doc, "items", None) or []):
            for attr in ("warehouse", "s_warehouse", "t_warehouse"):
                w = (getattr(line, attr, "") or "").strip()
                if w:
                    warehouse = w
                    break
            if warehouse:
                break

    if not warehouse:
        frappe.log_error(
            f"EFRIS multi-branch is enabled but no warehouse found on "
            f"{doc.doctype} {doc.name}. Falling back to default branch "
            f"from EFRIS Settings.",
            "EFRIS Multi-Branch — No Warehouse",
        )
        return (settings.branch_id or "", settings.branch_name or "")

    # ── Look up URA branch fields on the Warehouse ────────────────────────
    wh_data = frappe.db.get_value(
        "Warehouse",
        warehouse,
        ["efris_branch_id", "efris_branch_name"],
        as_dict=True,
    ) or {}

    wh_branch_id   = (wh_data.get("efris_branch_id")   or "").strip()
    wh_branch_name = (wh_data.get("efris_branch_name") or "").strip()

    if not wh_branch_id:
        frappe.throw(
            f"<b>EFRIS Multi-Branch</b> is enabled but warehouse "
            f"<b>{warehouse}</b> has no <b>URA Branch Id</b> configured.<br><br>"
            f"Go to <b>Warehouse → {warehouse} → URA</b> section and set "
            f"the <b>URA Branch Id</b> (assigned by URA for this branch).",
            title="EFRIS Branch ID Missing on Warehouse",
        )

    # ── Mutate settings in-place so all downstream callers see the branch ─
    settings.branch_id   = wh_branch_id
    settings.branch_name = wh_branch_name or settings.branch_name

    frappe.logger("efris").info(
        f"EFRIS multi-branch: resolved branch for {doc.doctype} {doc.name} "
        f"→ warehouse='{warehouse}' branch_id='{wh_branch_id}'"
    )

    return (wh_branch_id, wh_branch_name)


# ── QR code generator ─────────────────────────────────────────────────────────

def generate_qr_code_b64(data: str) -> str:
    """Generate a QR code PNG and return as a base64 data URI."""
    qr = qrcode.QRCode(box_size=4, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    img: Image.Image = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    import base64
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


# ── EFRIS request/response logger ────────────────────────────────────────────

def log_efris(
    document_type: str,
    document_name: str,
    interface_code: str,
    request_payload: dict,
    response_payload: dict,
    status: str,
    error_message: str = "",
):
    try:
        frappe.get_doc(
            {
                "doctype":          "EFRIS Log",
                "document_type":    document_type,
                "document_name":    document_name,
                "interface_code":   interface_code,
                "request_payload":  frappe.as_json(request_payload),
                "response_payload": frappe.as_json(response_payload),
                "status":           status,
                "error_message":    error_message,
                "timestamp":        now_str(),
            }
        ).insert(ignore_permissions=True)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "EFRIS Log Insert Failed")


# ── Type-safe coercions ───────────────────────────────────────────────────────

def safe_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def safe_str(value) -> str:
    return str(value) if value is not None else ""


def efris_num(value, decimal_places: int = 2) -> str:
    """
    Format a numeric value as a clean decimal string for EFRIS payloads.

    URA rule [665]: integer part max 12 digits, decimal part max 8 digits.
    Python's str(float(...)) can emit full-precision strings such as
    "770.4199999999999" (13 dp from floating-point residue in ERPNext
    Decimal fields) which URA rejects outright.

    This rounds to `decimal_places` (default 2 for prices / amounts) and
    strips trailing zeros so the result is always compact and valid:
        1000.0    → "1000"
        770.42    → "770.42"
        0.5       → "0.5"
        2.500000  → "2.5"
    """
    try:
        val = round(float(value or 0), decimal_places)
    except (TypeError, ValueError):
        val = 0.0
    # Fixed-point then strip trailing zeros and any dangling decimal point
    formatted = f"{val:.{decimal_places}f}".rstrip("0").rstrip(".")
    return formatted or "0"
