"""
T130 — Register Goods (Add new item on EFRIS)
T131 — Modify Goods  (Update existing item on EFRIS)

Mirrors: ItemMaster.cs → btnAdd_Click() / btnUpdate_Click()
"""

import re

import frappe

from efris_integration.api.base import EFRISClient, EFRISAPIError
from efris_integration.api.session import (
    SESSION_ERRORS,
    ensure_session,
    refresh_session,
    handle_session_error,
)
from efris_integration.utils.helpers import log_efris


# ─── Whitelisted entry point ───────────────────────────────────────────────────

@frappe.whitelist()
def upload_item(item_code: str, company: str) -> dict:
    """
    Upload or update an Item on EFRIS.
    Reads `efris_upload_type` to decide T130 (Add) vs T131 (Update).
    Every outcome — success or failure — is written to EFRIS Log.
    """
    if not item_code:
        frappe.throw("Item Code is required.")
    if not company:
        frappe.throw("Company is required.")

    item = frappe.get_doc("Item", item_code)
    upload_type = (item.get("efris_upload_type") or "").strip()

    if not upload_type:
        _log_item("Item", item.name, "T130", {}, {}, "Failed",
                  "Upload Type On EFRIS not set")
        frappe.throw(
            "Please set <b>Upload Type On EFRIS</b> (Add or Update) on the Item.",
            title="Upload Type Not Set",
        )

    if upload_type == "Add":
        return _register_goods(item, company)
    elif upload_type == "Update":
        return _modify_goods(item, company)
    else:
        _log_item("Item", item.name, "T130", {}, {}, "Failed",
                  f"Unknown upload type: {upload_type}")
        frappe.throw(f"Unknown Upload Type '{upload_type}'. Must be Add or Update.")


# ─── T130 Register Goods ───────────────────────────────────────────────────────

def _register_goods(item, company: str) -> dict:
    """T130 — Register new goods on EFRIS. Mirrors ItemMaster.cs → btnAdd_Click()"""
    client  = EFRISClient(company=company)
    payload = _build_goods_payload(item, company, operation="Add")

    try:
        ensure_session(client, company)
    except Exception as exc:
        _log_item("Item", item.name, "T130", payload, {}, "Failed",
                  f"Session load failed: {exc}")
        frappe.throw(str(exc), title="EFRIS Session Error")

    try:
        result = _call_t130(client, payload, item.name)

    except EFRISAPIError as exc:
        if exc.code not in SESSION_ERRORS:
            _log_item("Item", item.name, "T130", payload, {}, "Failed",
                      f"[{exc.code}] {exc.message}")
            frappe.throw(
                f"EFRIS T130 failed [<b>{exc.code}</b>]: {exc.message}",
                title="EFRIS T130 Error",
            )

        # Session stale — refresh via T104 (no T103, safe) and retry once.
        # Mirrors T126: auto-refresh on first stale-key hit, fail on second.
        frappe.logger("efris").info(
            f"T130 [{exc.code}] {item.name} [{company}] — session stale, refreshing."
        )
        _log_item("Item", item.name, "T130", payload, {}, "Failed",
                  f"Session stale [{exc.code}] — refreshing (T104) and retrying")

        try:
            refresh_session(client, company)
        except Exception as ref_exc:
            _log_item("Item", item.name, "T130", payload, {}, "Failed",
                      f"Session refresh failed: {ref_exc}")
            handle_session_error("T130", company, exc, "Item", item.name)

        try:
            result = _call_t130(client, payload, item.name)
        except EFRISAPIError as exc2:
            handle_session_error("T130", company, exc2, "Item", item.name)

    # Success — store returned goodsId.
    # EFRIS T130 returns a list; pick the first item's goodsId.
    goods_id = ""
    if isinstance(result, list) and result:
        first = result[0] if isinstance(result[0], dict) else {}
        goods_id = (
            first.get("goodsId") or first.get("GoodsId") or first.get("id") or ""
        )
    elif isinstance(result, dict):
        goods_id = (
            result.get("goodsId") or result.get("GoodsId") or result.get("id") or ""
        )
    if goods_id:
        frappe.db.set_value("Item", item.name, "efris_reference_no", str(goods_id))
        frappe.db.commit()

    _log_item("Item", item.name, "T130", payload, result, "Success")

    msg = f"Item <b>{item.item_code}</b> registered on EFRIS successfully."
    if goods_id:
        msg += f"<br>EFRIS Goods ID: <b>{goods_id}</b>"

    frappe.msgprint(msg, title="EFRIS T130 — Goods Registered", indicator="green")
    return {"status": "ok", "goods_id": goods_id, "message": msg}


def _call_t130(client: EFRISClient, payload: dict, doc_name: str) -> dict:
    # T130 spec: request body is an array [{...}]. AES encrypted + RSA signed.
    return client.call(
        "T130", payload=[payload],
        encrypt=True, sign=True,
        doc_type="Item", doc_name=doc_name,
    )


# ─── T131 Modify Goods ────────────────────────────────────────────────────────

def _modify_goods(item, company: str) -> dict:
    """T131 — Modify existing goods on EFRIS. Mirrors ItemMaster.cs → btnUpdate_Click()"""
    goods_id = (item.get("efris_reference_no") or "").strip()
    if not goods_id:
        _log_item("Item", item.name, "T131", {}, {}, "Failed",
                  "Reference No. (EFRIS Goods ID) is empty — upload with Add first")
        frappe.throw(
            "Cannot update: <b>Reference No.</b> (EFRIS Goods ID) is empty.<br>"
            "Upload the item with <b>Upload Type = Add</b> first.",
            title="EFRIS Goods ID Missing",
        )

    client  = EFRISClient(company=company)
    payload = _build_goods_payload(item, company, operation="Update", goods_id=goods_id)

    try:
        ensure_session(client, company)
    except Exception as exc:
        _log_item("Item", item.name, "T131", payload, {}, "Failed",
                  f"Session load failed: {exc}")
        frappe.throw(str(exc), title="EFRIS Session Error")

    try:
        result = _call_t131(client, payload, item.name)

    except EFRISAPIError as exc:
        if exc.code not in SESSION_ERRORS:
            _log_item("Item", item.name, "T131", payload, {}, "Failed",
                      f"[{exc.code}] {exc.message}")
            frappe.throw(
                f"EFRIS T131 failed [<b>{exc.code}</b>]: {exc.message}",
                title="EFRIS T131 Error",
            )

        frappe.logger("efris").info(
            f"T131 [{exc.code}] {item.name} [{company}] — session stale, refreshing."
        )
        _log_item("Item", item.name, "T131", payload, {}, "Failed",
                  f"Session stale [{exc.code}] — refreshing (T104) and retrying")

        try:
            refresh_session(client, company)
        except Exception as ref_exc:
            _log_item("Item", item.name, "T131", payload, {}, "Failed",
                      f"Session refresh failed: {ref_exc}")
            handle_session_error("T131", company, exc, "Item", item.name)

        try:
            result = _call_t131(client, payload, item.name)
        except EFRISAPIError as exc2:
            handle_session_error("T131", company, exc2, "Item", item.name)

    _log_item("Item", item.name, "T131", payload, result, "Success")

    msg = f"Item <b>{item.item_code}</b> updated on EFRIS successfully."
    frappe.msgprint(msg, title="EFRIS T131 — Goods Updated", indicator="green")
    return {"status": "ok", "message": msg}


def _call_t131(client: EFRISClient, payload: dict, doc_name: str) -> dict:
    # T131 spec: request body is an array [{...}]. AES encrypted + RSA signed.
    return client.call(
        "T131", payload=[payload],
        encrypt=True, sign=True,
        doc_type="Item", doc_name=doc_name,
    )


# ─── EFRIS Log helper ─────────────────────────────────────────────────────────

def _log_item(doc_type, doc_name, interface_code, request, response, status, error=""):
    """Write one EFRIS Log entry for a module-level goods operation outcome."""
    log_efris(doc_type, doc_name, interface_code, request, response, status, error)


# ─── Payload builder ──────────────────────────────────────────────────────────

def _build_goods_payload(item, company: str, operation: str, goods_id: str = "") -> dict:
    """
    Build the T130 / T131 JSON payload from Item fields.
    Mirrors ItemMaster.cs field mapping exactly.
    """
    uom_code = _get_efris_uom(item.stock_uom, company)

    def yn(val):
        return "101" if str(val or "").strip().lower() in ("yes", "1", "true") else "102"

    goods_type_code = {"Goods": "101", "Services": "102"}.get(
        str(item.get("efris_goods_type") or "Goods").strip(), "101"
    )

    raw_desc   = item.get("description") or ""
    plain_desc = re.sub(r"<[^>]+>", " ", raw_desc).strip()[:200]

    # All numeric fields are sent as strings per EFRIS T130 spec.
    # currency: T115 currencyType code — UGX = "101"
    have_piece = yn(item.get("efris_have_piece_unit"))
    have_excise = yn(item.get("efris_have_excise_tax"))

    piece_uom = str(item.get("efris_piece_uom") or "")

    payload = {
        "operationType":      "101" if operation == "Add" else "102",
        "goodsName":          str(item.item_name or item.item_code or ""),
        "goodsCode":          str(item.item_code or ""),
        "measureUnit":        uom_code,
        "unitPrice":          str(float(item.get("efris_unit_price") or 0)),
        "currency":           "101",                  # UGX per T115 currencyType
        "commodityCategoryId": str(item.get("efris_goods_category_id") or ""),
        "haveExciseTax":      have_excise,
        "description":        plain_desc,
        "stockPrewarning":    str(float(item.get("efris_stock_prewarning") or 0)),
        "pieceMeasureUnit":   piece_uom if have_piece == "101" else "",
        "havePieceUnit":      have_piece,
        "pieceUnitPrice":     str(float(item.get("efris_piece_unit_price") or 0)) if have_piece == "101" else "",
        "packageScaledValue": "1",
        "pieceScaledValue":   str(float(item.get("efris_piece_scaled_value") or 0)) if have_piece == "101" else "",
        "exciseDutyCode":     str(item.get("efris_excise_duty_category") or "") if have_excise == "101" else "",
        "haveOtherUnit":      "102",                  # no other unit
        "goodsTypeCode":      goods_type_code,
        "haveCustomsUnit":    "102",                  # no customs unit (required field)
        "remarks":            str(item.get("efris_item_remark") or ""),
    }

    if operation == "Update":
        payload["goodsId"] = goods_id

    return payload


# ─── UOM mapping ──────────────────────────────────────────────────────────────

def _get_efris_uom(erpnext_uom: str, company: str) -> str:
    """Map ERPNext UOM → EFRIS URA UOM code. Falls back to 'PP' (Piece)."""
    if not erpnext_uom:
        return "PP"

    ura_uom = frappe.db.get_value(
        "EFRIS UOM Mapping", {"company": company, "code": erpnext_uom}, "ura_uom"
    ) or frappe.db.get_value(
        "EFRIS UOM Mapping", {"company": company, "uom_name": erpnext_uom}, "ura_uom"
    )
    if ura_uom:
        return str(ura_uom).strip()

    return {
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
    }.get(erpnext_uom, "PP")
