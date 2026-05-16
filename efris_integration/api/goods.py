"""
T130 — Register Goods (Add new item on EFRIS)
T131 — Modify Goods  (Update existing item on EFRIS)

Mirrors: ItemMaster.cs → btnAdd_Click() / btnUpdate_Click()

SAP B1 flow (ItemMaster.cs):
  1. Read item UDFs (goods code, name, UOM, price, category, excise flags, …)
  2. Build JSON payload
  3. AES-encrypt + RSA-sign
  4. POST T130 (Add) or T131 (Update) — same endpoint, different interfaceCode
  5. Parse returnCode: "00" = success, anything else = failure

EFRIS Yes/No codes (used in haveExciseTax, havePieceUnit):
  "101" = Yes
  "102" = No

The "Upload Type On EFRIS" field on Item drives the choice:
  "Add"    → T130 RegisterGoods
  "Update" → T131 ModifyGoods
"""

import frappe

from efris_integration.api.base import EFRISClient, EFRISAPIError
from efris_integration.utils.helpers import get_efris_settings


# ─── Whitelisted entry point (called from item.js button) ─────────────────────

@frappe.whitelist()
def upload_item(item_code: str, company: str) -> dict:
    """
    Upload or update an Item on EFRIS.

    Called from the "Upload to EFRIS" button on the Item form.
    Reads `efris_upload_type` from the Item to decide T130 vs T131.

    Returns {"status": "ok", "message": "..."} on success.
    Raises frappe.ValidationError on failure.
    """
    if not item_code:
        frappe.throw("Item Code is required.")
    if not company:
        frappe.throw("Company is required.")

    item = frappe.get_doc("Item", item_code)

    upload_type = (item.get("efris_upload_type") or "").strip()
    if not upload_type:
        frappe.throw(
            "Please set <b>Upload Type On EFRIS</b> (Add or Update) on the Item "
            "before uploading.",
            title="Upload Type Not Set",
        )

    if upload_type == "Add":
        return _register_goods(item, company)
    elif upload_type == "Update":
        return _modify_goods(item, company)
    else:
        frappe.throw(f"Unknown Upload Type '{upload_type}'. Must be 'Add' or 'Update'.")


# ─── T130 Register Goods ───────────────────────────────────────────────────────

def _register_goods(item, company: str) -> dict:
    """
    T130 — Register new goods on EFRIS.
    Mirrors ItemMaster.cs → btnAdd_Click()
    """
    client  = EFRISClient(company=company)
    payload = _build_goods_payload(item, company, operation="Add")

    try:
        result = client.call(
            "T130",
            payload=payload,
            encrypt=True,
            sign=True,
            doc_type="Item",
            doc_name=item.name,
        )
    except EFRISAPIError as exc:
        frappe.throw(
            f"EFRIS T130 Register Goods failed [{exc.code}]: {exc.message}",
            title="EFRIS Upload Failed",
        )

    # SAP B1 reads back the EFRIS-assigned goodsId after T130 and stores it.
    # We do the same — store it in efris_reference_no if EFRIS returns one.
    goods_id = (
        result.get("goodsId")
        or result.get("GoodsId")
        or result.get("id")
        or ""
    )
    if goods_id:
        frappe.db.set_value("Item", item.name, "efris_reference_no", str(goods_id))
        frappe.db.commit()

    msg = f"Item <b>{item.item_code}</b> registered on EFRIS successfully."
    if goods_id:
        msg += f" EFRIS Goods ID: <b>{goods_id}</b>"

    frappe.msgprint(msg, title="EFRIS T130 — Goods Registered", indicator="green")
    return {"status": "ok", "goods_id": goods_id, "message": msg}


# ─── T131 Modify Goods ────────────────────────────────────────────────────────

def _modify_goods(item, company: str) -> dict:
    """
    T131 — Modify existing goods on EFRIS.
    Mirrors ItemMaster.cs → btnUpdate_Click()
    """
    # SAP B1 reads the EFRIS goodsId from the stored UDF before updating.
    # We read it from efris_reference_no.
    goods_id = (item.get("efris_reference_no") or "").strip()
    if not goods_id:
        frappe.throw(
            "Cannot update: <b>Reference No.</b> (EFRIS Goods ID) is empty.<br>"
            "Upload the item with <b>Upload Type = Add</b> first to register it "
            "and obtain the EFRIS Goods ID.",
            title="EFRIS Goods ID Missing",
        )

    client  = EFRISClient(company=company)
    payload = _build_goods_payload(item, company, operation="Update", goods_id=goods_id)

    try:
        result = client.call(
            "T131",
            payload=payload,
            encrypt=True,
            sign=True,
            doc_type="Item",
            doc_name=item.name,
        )
    except EFRISAPIError as exc:
        frappe.throw(
            f"EFRIS T131 Modify Goods failed [{exc.code}]: {exc.message}",
            title="EFRIS Update Failed",
        )

    msg = f"Item <b>{item.item_code}</b> updated on EFRIS successfully."
    frappe.msgprint(msg, title="EFRIS T131 — Goods Updated", indicator="green")
    return {"status": "ok", "message": msg}


# ─── Payload builder ───────────────────────────────────────────────────────────

def _build_goods_payload(item, company: str, operation: str, goods_id: str = "") -> dict:
    """
    Build the T130 / T131 JSON payload from the Item document.

    Mirrors ItemMaster.cs field mapping:
        goodsCode          ← item_code
        goodsName          ← item_name
        measureUnit        ← stock_uom  (mapped via EFRIS UOM Mapping)
        unitPrice          ← efris_unit_price
        currency           ← "UGX"  (EFRIS always uses UGX)
        commodityCategoryId← efris_goods_category_id
        haveExciseTax      ← efris_have_excise_tax  ("Yes"→"101", "No"→"102")
        description        ← description (plain text, first 200 chars)
        stockPrewarning    ← efris_stock_prewarning
        pieceMeasureUnit   ← efris_piece_uom
        havePieceUnit      ← efris_have_piece_unit  ("Yes"→"101", "No"→"102")
        pieceUnitPrice     ← efris_piece_unit_price
        exciseDutyCategoryCode ← efris_excise_duty_category
        exciseUnitPrice    ← efris_excise_rate_usd
        exciseRatio        ← efris_excise_rate
        exciseDutyCode     ← efris_excise_duty_category
        pieceScaledValue   ← efris_piece_scaled_value
        goodsTypeCode      ← efris_goods_type  (Goods→"101", Services→"102")
        operationType      ← "101"=Add / "102"=Update  (T131 only)
        goodsId            ← efris_reference_no  (T131 only)
    """
    # ── UOM ────────────────────────────────────────────────────────────────
    uom_code = _get_efris_uom(item.stock_uom, company)

    # ── Yes/No → EFRIS codes ───────────────────────────────────────────────
    def yn(val):
        return "101" if str(val or "").strip().lower() in ("yes", "1", "true") else "102"

    # ── Goods Type → EFRIS code ────────────────────────────────────────────
    goods_type_map = {"Goods": "101", "Services": "102"}
    goods_type_code = goods_type_map.get(
        str(item.get("efris_goods_type") or "Goods").strip(), "101"
    )

    # ── Description — strip HTML tags ─────────────────────────────────────
    import re
    raw_desc = item.get("description") or ""
    plain_desc = re.sub(r"<[^>]+>", " ", raw_desc).strip()[:200]

    payload = {
        "goodsCode":          str(item.item_code or ""),
        "goodsName":          str(item.item_name or item.item_code or ""),
        "measureUnit":        uom_code,
        "unitPrice":          float(item.get("efris_unit_price") or 0),
        "currency":           "UGX",
        "commodityCategoryId":str(item.get("efris_goods_category_id") or ""),
        "haveExciseTax":      yn(item.get("efris_have_excise_tax")),
        "description":        plain_desc,
        "stockPrewarning":    float(item.get("efris_stock_prewarning") or 0),
        "pieceMeasureUnit":   str(item.get("efris_piece_uom") or ""),
        "havePieceUnit":      yn(item.get("efris_have_piece_unit")),
        "pieceUnitPrice":     float(item.get("efris_piece_unit_price") or 0),
        "packageScaledValue": 0,
        "pieceScaledValue":   float(item.get("efris_piece_scaled_value") or 0),
        "exciseDutyCategoryCode": str(item.get("efris_excise_duty_category") or ""),
        "exciseUnitPrice":    float(item.get("efris_excise_rate_usd") or 0),
        "exciseRatio":        float(item.get("efris_excise_rate") or 0),
        "exciseDutyCode":     str(item.get("efris_excise_duty_category") or ""),
        "goodsTypeCode":      goods_type_code,
        "remarks":            str(item.get("efris_item_remark") or ""),
    }

    # T131 (Update) needs operation type and the EFRIS goods ID
    if operation == "Update":
        payload["operationType"] = "102"
        payload["goodsId"]       = goods_id
    else:
        # T130 (Add) — operationType "101" is Add in SAP B1
        payload["operationType"] = "101"

    return payload


# ─── UOM mapping helper ────────────────────────────────────────────────────────

def _get_efris_uom(erpnext_uom: str, company: str) -> str:
    """
    Map an ERPNext UOM name to the EFRIS / URA UOM code.

    Lookup order:
      1. EFRIS UOM Mapping doctype (company-specific, matched by code or uom_name)
      2. Fallback: well-known defaults
      3. Final fallback: "PP" (Piece — most common EFRIS default)

    EFRIS standard UOM codes (from URA documentation):
      PP  = Piece / Unit
      KGM = Kilogram
      LTR = Litre
      MTR = Metre
      BX  = Box
      DZ  = Dozen
      BE  = Bundle
    """
    if not erpnext_uom:
        return "PP"

    # 1. EFRIS UOM Mapping table
    ura_uom = frappe.db.get_value(
        "EFRIS UOM Mapping",
        {"company": company, "code": erpnext_uom},
        "ura_uom",
    )
    if not ura_uom:
        ura_uom = frappe.db.get_value(
            "EFRIS UOM Mapping",
            {"company": company, "uom_name": erpnext_uom},
            "ura_uom",
        )
    if ura_uom:
        return str(ura_uom).strip()

    # 2. Built-in defaults (covers most common UOMs without requiring a mapping row)
    _defaults = {
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
    return _defaults.get(erpnext_uom, "PP")
