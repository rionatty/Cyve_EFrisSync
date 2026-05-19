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

def map_uom(erpnext_uom: str, company: str) -> str:
    """
    Map an ERPNext UOM to its URA UOM code for the given company.
    Looks up EFRIS UOM Mapping by company + code field.
    Falls back to the raw UOM string if no mapping found.
    """
    row = frappe.db.get_value(
        "EFRIS UOM Mapping",
        {"company": company, "code": erpnext_uom},
        "ura_uom",
    )
    if row:
        return row
    # Try matching by uom_name as fallback
    row = frappe.db.get_value(
        "EFRIS UOM Mapping",
        {"company": company, "uom_name": erpnext_uom},
        "ura_uom",
    )
    return row or erpnext_uom


# ── Tax code mapping (per-company) ────────────────────────────────────────────

def map_tax_code(erpnext_tax: str, company: str) -> str:
    """
    Map an ERPNext tax code/account to its URA Tax Code for the given company.
    Returns the URA tax code (e.g. "18", "0", "-").
    Falls back to "18" (standard VAT) if not mapped.
    """
    row = frappe.db.get_value(
        "EFRIS Tax Mapping",
        {"company": company, "erpnext_tax_code": erpnext_tax},
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
