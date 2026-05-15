"""
General-purpose helpers for EFRIS integration.
"""

import io
import uuid
from datetime import datetime

import frappe
import qrcode
from PIL import Image


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def generate_data_exchange_id() -> str:
    return str(uuid.uuid4()).replace("-", "")[:16]


def make_global_info(interface_code: str, settings) -> dict:
    return {
        "appId": settings.app_id,
        "brn": settings.branch_id or "",
        "dataExchangeId": generate_data_exchange_id(),
        "deviceMAC": "1",
        "deviceNo": settings.device_no,
        "longitude": "0",
        "latitude": "0",
        "interfaceCode": interface_code,
        "requestCode": "TP",
        "requestTime": now_str(),
        "responseCode": "TA",
        "taxpayerID": settings.taxpayer_id,
        "tin": settings.tin,
        "userName": settings.efris_username,
        "version": settings.api_version or "1.1.20191201",
    }


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
                "doctype": "EFRIS Log",
                "document_type": document_type,
                "document_name": document_name,
                "interface_code": interface_code,
                "request_payload": frappe.as_json(request_payload),
                "response_payload": frappe.as_json(response_payload),
                "status": status,
                "error_message": error_message,
                "timestamp": now_str(),
            }
        ).insert(ignore_permissions=True)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "EFRIS Log Insert Failed")


def generate_qr_code_b64(data: str) -> str:
    """Generate a QR code PNG and return as base64 data URI."""
    qr = qrcode.QRCode(box_size=4, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    img: Image.Image = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    import base64

    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def get_efris_settings() -> "frappe.Document":
    settings = frappe.get_single("EFRIS Settings")
    if not settings.api_url:
        frappe.throw("EFRIS Settings are not configured. Please configure via EFRIS Settings.")
    return settings


def map_tax_code(erpnext_tax: str) -> str:
    """Map ERPNext tax template/account to EFRIS tax category code."""
    row = frappe.db.get_value(
        "EFRIS Tax Mapping",
        {"erpnext_tax": erpnext_tax},
        "efris_tax_code",
    )
    return row or "101"  # default: standard 18% VAT


def map_uom(erpnext_uom: str) -> str:
    """Map ERPNext UOM to EFRIS UOM code."""
    row = frappe.db.get_value(
        "EFRIS UOM Mapping",
        {"erpnext_uom": erpnext_uom},
        "efris_uom_code",
    )
    return row or "101"  # default: piece


def get_item_efris_fields(item_code: str) -> dict:
    return frappe.db.get_value(
        "Item",
        item_code,
        [
            "efris_goods_category_id",
            "efris_excise_flag",
            "efris_excise_rate",
            "efris_excise_rate_usd",
            "efris_weight_unit",
            "weight_per_unit",
            "weight_uom",
        ],
        as_dict=True,
    ) or {}


def get_customer_efris_fields(customer: str) -> dict:
    return frappe.db.get_value(
        "Customer",
        customer,
        [
            "tax_id",
            "efris_buyer_type",
            "efris_nin_brn",
            "efris_citizenship",
            "efris_sector",
            "efris_export_flag",
            "mobile_no",
            "email_id",
        ],
        as_dict=True,
    ) or {}


def get_supplier_efris_fields(supplier: str) -> dict:
    return frappe.db.get_value(
        "Supplier",
        supplier,
        ["tax_id", "efris_nin_brn", "mobile_no", "email_id"],
        as_dict=True,
    ) or {}


def safe_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def safe_str(value) -> str:
    return str(value) if value is not None else ""
