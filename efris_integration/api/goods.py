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

Session error codes (same as T126 exchange rate):
  "15"   = EFRIS PKCS7 unpad failed — our AES key is wrong/stale
  "2066" = "Illegal json format" — EFRIS decrypted to garbage (wrong key,
           PKCS7 passed by chance but result isn't valid JSON)
  "16"   = Signature verification error
  "99"   = General session error
On these, we refresh the session (T103 → T104) once and retry.
"""

import frappe

from efris_integration.api.base import EFRISClient, EFRISAPIError

# EFRIS error codes that mean our AES session key is wrong or stale
_SESSION_ERRORS = {"15", "2066", "16", "99", "SESSION_EXPIRED"}


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

    Session handling:
      Load existing AES key from DB (same as SAP B1 reading SessionAESKey.txt).
      If EFRIS returns a session-error code (15/2066/16/99), refresh session
      once (T103 → T104) and retry — identical to exchange_rate.py logic.
    """
    client = EFRISClient(company=company)
    _ensure_session(client, company)

    payload = _build_goods_payload(item, company, operation="Add")

    try:
        result = _call_t130(client, payload, item.name)
    except EFRISAPIError as exc:
        if exc.code not in _SESSION_ERRORS:
            _throw_goods_error("T130", exc)

        # Session stale → refresh once and retry
        frappe.logger("efris").info(
            f"T130 [{exc.code}] for item {item.name} [{company}] — "
            "session stale, refreshing."
        )
        try:
            _refresh_session(client, company)
        except Exception as ref_exc:
            frappe.throw(
                f"EFRIS session refresh failed: {ref_exc}<br><br>"
                "Paste the current <b>SessionAESKey.txt</b> content into "
                "<b>EFRIS Settings → Session AES Key</b> and try again.",
                title="Session Refresh Failed",
            )

        try:
            result = _call_t130(client, payload, item.name)
        except EFRISAPIError as exc2:
            _throw_session_key_error("T130", exc2, company)

    # SAP B1 reads back the EFRIS-assigned goodsId after T130 and stores it.
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
        msg += f"<br>EFRIS Goods ID: <b>{goods_id}</b>"

    frappe.msgprint(msg, title="EFRIS T130 — Goods Registered", indicator="green")
    return {"status": "ok", "goods_id": goods_id, "message": msg}


def _call_t130(client: EFRISClient, payload: dict, doc_name: str) -> dict:
    return client.call(
        "T130",
        payload=payload,
        encrypt=True,
        sign=True,
        doc_type="Item",
        doc_name=doc_name,
    )


# ─── T131 Modify Goods ────────────────────────────────────────────────────────

def _modify_goods(item, company: str) -> dict:
    """
    T131 — Modify existing goods on EFRIS.
    Mirrors ItemMaster.cs → btnUpdate_Click()
    """
    goods_id = (item.get("efris_reference_no") or "").strip()
    if not goods_id:
        frappe.throw(
            "Cannot update: <b>Reference No.</b> (EFRIS Goods ID) is empty.<br>"
            "Upload the item with <b>Upload Type = Add</b> first to register it "
            "and obtain the EFRIS Goods ID.",
            title="EFRIS Goods ID Missing",
        )

    client = EFRISClient(company=company)
    _ensure_session(client, company)

    payload = _build_goods_payload(item, company, operation="Update", goods_id=goods_id)

    try:
        result = _call_t131(client, payload, item.name)
    except EFRISAPIError as exc:
        if exc.code not in _SESSION_ERRORS:
            _throw_goods_error("T131", exc)

        frappe.logger("efris").info(
            f"T131 [{exc.code}] for item {item.name} [{company}] — "
            "session stale, refreshing."
        )
        try:
            _refresh_session(client, company)
        except Exception as ref_exc:
            frappe.throw(
                f"EFRIS session refresh failed: {ref_exc}<br><br>"
                "Paste the current <b>SessionAESKey.txt</b> content into "
                "<b>EFRIS Settings → Session AES Key</b> and try again.",
                title="Session Refresh Failed",
            )

        try:
            result = _call_t131(client, payload, item.name)
        except EFRISAPIError as exc2:
            _throw_session_key_error("T131", exc2, company)

    msg = f"Item <b>{item.item_code}</b> updated on EFRIS successfully."
    frappe.msgprint(msg, title="EFRIS T131 — Goods Updated", indicator="green")
    return {"status": "ok", "message": msg}


def _call_t131(client: EFRISClient, payload: dict, doc_name: str) -> dict:
    return client.call(
        "T131",
        payload=payload,
        encrypt=True,
        sign=True,
        doc_type="Item",
        doc_name=doc_name,
    )


# ─── Session helpers (same pattern as exchange_rate.py) ───────────────────────

def _ensure_session(client: EFRISClient, company: str):
    """
    Load the existing session AES key from DB before calling T130/T131.
    Mirrors SAP B1: read SessionAESKey.txt directly, no T103/T104 proactively.
    Only runs T103→T104 if no key is stored at all (first run).
    """
    try:
        client._aes_key = client.key_manager.get_aes_key()
        frappe.logger("efris").info(
            f"[{company}] goods session key loaded: {len(client._aes_key) * 8}-bit"
        )
        return
    except RuntimeError:
        pass  # no key stored → need to initialise

    frappe.logger("efris").info(
        f"[{company}] no session key stored — running T103+T104 to initialise"
    )
    _refresh_session(client, company)
    client._aes_key = None
    client._aes_key = client.key_manager.get_aes_key()


def _refresh_session(client: EFRISClient, company: str):
    """
    Mid-call session refresh: T104 ONLY (forced, bypassing step-0 probe).
    Called when T130/T131 returns a session-error code (15/2066/16/99).

    CRITICAL: Do NOT call T103 (sign_in) here.
    T103 resets the EFRIS session AES key on the server.  If T104 then fails
    (e.g. because the private key in EFRIS Settings doesn't match the one
    registered with URA), we end up with NO valid key — and SAP B1's stored
    key is also now invalid.  By skipping T103 we avoid making things worse;
    if the session is still active on EFRIS's side, T104 alone can refresh the
    key without disturbing SAP B1's session.
    """
    from efris_integration.api.auth import get_symmetric_key

    # force=True: always call T104, skip the step-0 probe
    get_symmetric_key(company, force=True)
    client._aes_key = None  # force reload from DB on next call


def _throw_goods_error(t_code: str, exc: EFRISAPIError):
    """Raise a clean, actionable frappe error for a non-session EFRIS failure."""
    frappe.throw(
        f"EFRIS {t_code} failed [<b>{exc.code}</b>]: {exc.message}",
        title=f"EFRIS {t_code} Error",
    )


def _throw_session_key_error(t_code: str, exc: EFRISAPIError, company: str):
    """
    Raise an actionable error when error 15/2066 persists even after session refresh.

    Error 15 = "Data decryption error" — EFRIS cannot decrypt our AES-encrypted
    payload because the Session AES Key stored in EFRIS Settings does not match
    the key currently active on the EFRIS server.

    The only fix is to paste the correct key from SAP B1's SessionAESKey.txt into
    EFRIS Settings.  Do NOT call T103 (sign-in) from ERPNext first — that would
    reset the EFRIS session and invalidate the SAP B1 key too.
    """
    if exc.code in _SESSION_ERRORS:
        frappe.throw(
            f"<b>EFRIS {t_code} — Session AES Key mismatch [error {exc.code}]</b><br><br>"
            "EFRIS cannot decrypt the request because the <b>Session AES Key</b> in "
            f"EFRIS Settings for <b>{company}</b> does not match the active key on "
            "the EFRIS server.<br><br>"
            "<b>How to fix:</b><br>"
            "1. Open SAP Business One on the EFRIS server.<br>"
            "2. Copy the contents of <b>SessionAESKey.txt</b>.<br>"
            "3. In ERPNext open <b>EFRIS Settings</b> → <b>Session AES Key</b> "
            "and paste the copied value, then save.<br>"
            "4. <b>Do NOT</b> click 'Full Initialise' or 'T103 Sign-In' before "
            "uploading — that resets the EFRIS session and invalidates the key.<br>"
            "5. Try uploading the item again.",
            title="Session AES Key Mismatch",
        )
    _throw_goods_error(t_code, exc)


# ─── Payload builder ───────────────────────────────────────────────────────────

def _build_goods_payload(item, company: str, operation: str, goods_id: str = "") -> dict:
    """
    Build the T130 / T131 JSON payload from the Item document.

    Mirrors ItemMaster.cs field mapping:
        goodsCode           ← item_code
        goodsName           ← item_name
        measureUnit         ← stock_uom  (mapped via EFRIS UOM Mapping)
        unitPrice           ← efris_unit_price
        currency            ← "UGX"  (EFRIS always uses UGX)
        commodityCategoryId ← efris_goods_category_id
        haveExciseTax       ← efris_have_excise_tax  ("Yes"→"101", "No"→"102")
        description         ← description (plain text, first 200 chars)
        stockPrewarning     ← efris_stock_prewarning
        pieceMeasureUnit    ← efris_piece_uom
        havePieceUnit       ← efris_have_piece_unit  ("Yes"→"101", "No"→"102")
        pieceUnitPrice      ← efris_piece_unit_price
        exciseDutyCategoryCode ← efris_excise_duty_category
        exciseUnitPrice     ← efris_excise_rate_usd
        exciseRatio         ← efris_excise_rate
        exciseDutyCode      ← efris_excise_duty_category
        pieceScaledValue    ← efris_piece_scaled_value
        goodsTypeCode       ← efris_goods_type  (Goods→"101", Services→"102")
        operationType       ← "101"=Add / "102"=Update
        goodsId             ← efris_reference_no  (T131 Update only)
    """
    uom_code = _get_efris_uom(item.stock_uom, company)

    def yn(val):
        """Convert Yes/No/1/0 to EFRIS 101/102."""
        return "101" if str(val or "").strip().lower() in ("yes", "1", "true") else "102"

    goods_type_map = {"Goods": "101", "Services": "102"}
    goods_type_code = goods_type_map.get(
        str(item.get("efris_goods_type") or "Goods").strip(), "101"
    )

    # Strip HTML from description (item.description may contain HTML)
    import re
    raw_desc = item.get("description") or ""
    plain_desc = re.sub(r"<[^>]+>", " ", raw_desc).strip()[:200]

    payload = {
        "goodsCode":              str(item.item_code or ""),
        "goodsName":              str(item.item_name or item.item_code or ""),
        "measureUnit":            uom_code,
        "unitPrice":              float(item.get("efris_unit_price") or 0),
        "currency":               "UGX",
        "commodityCategoryId":    str(item.get("efris_goods_category_id") or ""),
        "haveExciseTax":          yn(item.get("efris_have_excise_tax")),
        "description":            plain_desc,
        "stockPrewarning":        float(item.get("efris_stock_prewarning") or 0),
        "pieceMeasureUnit":       str(item.get("efris_piece_uom") or ""),
        "havePieceUnit":          yn(item.get("efris_have_piece_unit")),
        "pieceUnitPrice":         float(item.get("efris_piece_unit_price") or 0),
        "packageScaledValue":     0,
        "pieceScaledValue":       float(item.get("efris_piece_scaled_value") or 0),
        "exciseDutyCategoryCode": str(item.get("efris_excise_duty_category") or ""),
        "exciseUnitPrice":        float(item.get("efris_excise_rate_usd") or 0),
        "exciseRatio":            float(item.get("efris_excise_rate") or 0),
        "exciseDutyCode":         str(item.get("efris_excise_duty_category") or ""),
        "goodsTypeCode":          goods_type_code,
        "remarks":                str(item.get("efris_item_remark") or ""),
        "operationType":          "101" if operation == "Add" else "102",
    }

    if operation == "Update":
        payload["goodsId"] = goods_id

    return payload


# ─── UOM mapping helper ────────────────────────────────────────────────────────

def _get_efris_uom(erpnext_uom: str, company: str) -> str:
    """
    Map an ERPNext UOM name to the EFRIS / URA UOM code.

    Lookup order:
      1. EFRIS UOM Mapping doctype (company-specific, by code then by name)
      2. Built-in fallback table for the most common UOMs
      3. Final fallback: "PP" (Piece — most common EFRIS default)
    """
    if not erpnext_uom:
        return "PP"

    # 1. EFRIS UOM Mapping table (company-specific)
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

    # 2. Built-in defaults
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
