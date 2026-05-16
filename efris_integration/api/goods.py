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

    # ── Success: extract URA internal goods ID from response ──────────────
    # T130 response is a list; the URA internal number is goodsId per item.
    goods_id = ""
    if isinstance(result, list) and result:
        first = result[0] if isinstance(result[0], dict) else {}
        goods_id = str(
            first.get("goodsId") or first.get("GoodsId")
            or first.get("commodityGoodsId") or first.get("id") or ""
        ).strip()
    elif isinstance(result, dict):
        goods_id = str(
            result.get("goodsId") or result.get("GoodsId")
            or result.get("commodityGoodsId") or result.get("id") or ""
        ).strip()

    # ── Write back to Item ─────────────────────────────────────────────────
    success_remark = "Good WOA! Item Created Successfully on URA EFRIS."
    updates = {
        "efris_upload_type":  "Update",      # next upload auto-uses T131
        "efris_item_remark":  success_remark,
    }
    if goods_id:
        updates["efris_reference_no"] = goods_id   # URA internal number → Reference No.

    for field, val in updates.items():
        frappe.db.set_value("Item", item.name, field, val, update_modified=False)
    frappe.db.commit()

    _log_item("Item", item.name, "T130", payload, result, "Success")

    msg = (
        f"Good WOA! Item <b>{item.item_code}</b> Created Successfully on URA EFRIS."
    )
    if goods_id:
        msg += f"<br>URA Reference No.: <b>{goods_id}</b>"

    frappe.msgprint(msg, title="EFRIS T130 — Goods Registered", indicator="green")
    return {"status": "ok", "goods_id": goods_id, "message": msg}


def _call_t130(client: EFRISClient, payload: dict, doc_name: str) -> dict:
    # T130 spec: request body is an array [{...}]. AES encrypted + RSA signed.
    return client.call(
        "T130", payload=[payload],
        encrypt=True, sign=True,
        doc_type="Item", doc_name=doc_name,
    )


# ─── T130 Modify Goods (operationType 102) ───────────────────────────────────

def _modify_goods(item, company: str) -> dict:
    """
    T130 (operationType=102) — Modify existing goods on EFRIS.
    Mirrors ItemMaster.cs → btnUpdate_Click()

    EFRIS API note: T130 handles both Add (operationType=101) and Modify
    (operationType=102).  T131 is "Goods Stock Maintain" (inventory) — a
    completely different operation with a different payload.  Sending a
    T130-style goods payload to T131 returns error 2066 "Illegal json format".
    """
    # goodsId (EFRIS internal number) is optional — EFRIS also accepts goodsCode
    # (the ERPNext item code) as the unique identifier for the modify operation.
    goods_id = (item.get("efris_reference_no") or "").strip()

    client  = EFRISClient(company=company)
    payload = _build_goods_payload(item, company, operation="Update", goods_id=goods_id)

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
                f"EFRIS T130 (modify) failed [<b>{exc.code}</b>]: {exc.message}",
                title="EFRIS T130 Error",
            )

        frappe.logger("efris").info(
            f"T130-modify [{exc.code}] {item.name} [{company}] — session stale, refreshing."
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

    # ── Success: extract URA internal goods ID from response ──────────────
    goods_id_new = ""
    if isinstance(result, list) and result:
        first = result[0] if isinstance(result[0], dict) else {}
        goods_id_new = str(
            first.get("goodsId") or first.get("GoodsId")
            or first.get("commodityGoodsId") or first.get("id") or ""
        ).strip()
    elif isinstance(result, dict):
        goods_id_new = str(
            result.get("goodsId") or result.get("GoodsId")
            or result.get("commodityGoodsId") or result.get("id") or ""
        ).strip()

    # ── Write back to Item ─────────────────────────────────────────────────
    success_remark = "Good WOA! Item Updated Successfully on URA EFRIS."
    updates = {
        "efris_item_remark": success_remark,
    }
    if goods_id_new:
        updates["efris_reference_no"] = goods_id_new

    for field, val in updates.items():
        frappe.db.set_value("Item", item.name, field, val, update_modified=False)
    frappe.db.commit()

    _log_item("Item", item.name, "T130", payload, result, "Success")

    msg = f"Good WOA! Item <b>{item.item_code}</b> Updated Successfully on URA EFRIS."
    if goods_id_new:
        msg += f"<br>URA Reference No.: <b>{goods_id_new}</b>"

    frappe.msgprint(msg, title="EFRIS T130 — Goods Updated", indicator="green")
    return {"status": "ok", "goods_id": goods_id_new, "message": msg}


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

    # pieceMeasureUnit also goes through the same UOM mapper (max 3 chars enforced)
    piece_uom_raw = str(item.get("efris_piece_uom") or "")
    piece_uom = _get_efris_uom(piece_uom_raw, company) if piece_uom_raw else ""

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
        "packageScaledValue": "1" if have_piece == "101" else "",
        "pieceScaledValue":   str(float(item.get("efris_piece_scaled_value") or 0)) if have_piece == "101" else "",
        "exciseDutyCode":     str(item.get("efris_excise_duty_category") or "") if have_excise == "101" else "",
        "haveOtherUnit":      "102",                  # no other unit
        "goodsTypeCode":      goods_type_code,
        "haveCustomsUnit":    "102",                  # no customs unit (required field)
        "remarks":            str(item.get("efris_item_remark") or ""),
    }

    if operation == "Update" and goods_id:
        # Include EFRIS internal goodsId only when available.
        # If empty, EFRIS identifies the item by goodsCode (ERPNext item code).
        payload["goodsId"] = goods_id

    return payload


# ─── UOM mapping ──────────────────────────────────────────────────────────────

def _get_efris_uom(erpnext_uom: str, company: str) -> str:
    """
    Map ERPNext UOM → EFRIS URA UOM code (max 3 chars per EFRIS spec).
    Falls back to 'PP' (Piece) if not mapped or if the mapped value is > 3 chars.
    """
    if not erpnext_uom:
        return "PP"

    ura_uom = frappe.db.get_value(
        "EFRIS UOM Mapping", {"company": company, "code": erpnext_uom}, "ura_uom"
    ) or frappe.db.get_value(
        "EFRIS UOM Mapping", {"company": company, "uom_name": erpnext_uom}, "ura_uom"
    )
    if ura_uom:
        code = str(ura_uom).strip()
        if len(code) <= 3:
            return code
        # Mapped value is too long — log and fall through to built-in map
        frappe.logger("efris").warning(
            f"EFRIS UOM Mapping '{erpnext_uom}' → '{code}' is {len(code)} chars "
            f"(max 3). Check EFRIS UOM Mapping for company '{company}'."
        )

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
