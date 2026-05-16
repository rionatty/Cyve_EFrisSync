"""
T131 — Goods Stock Maintain (Stock In / Inventory Adjustment)

Submits a Stock Entry's items as a stock-in record directly to EFRIS.
This is the dedicated inventory-management interface — NOT the same as T114
(Goods Receipt linked to a purchase/invoice flow).

Payload structure (T131 spec, pages 172-175):
  {
    "goodsStockIn":     { header fields },
    "goodsStockInItem": [ { per-item fields }, ... ]
  }

stockInType mapping (from efris_stock_in_type label field):
  "Import"                 → "101"
  "Local Purchase"         → "102"
  "Manufacture/Assembling" → "103"
  "Opening Stock"          → "104"

Business rules from spec:
  - If stockInType = 103 (Manufacture): supplierTin + supplierName must be empty
  - supplierName is required when operationType = 101 (stock-in) and not Manufacture
  - commodityGoodsId and goodsCode cannot BOTH be empty (we always send goodsCode)
"""

import frappe

from efris_integration.api.base import EFRISClient, EFRISAPIError
from efris_integration.api.session import (
    SESSION_ERRORS,
    ensure_session,
    refresh_session,
    handle_session_error,
)
from efris_integration.utils.helpers import log_efris

# ─── stockInType label → EFRIS code ──────────────────────────────────────────

STOCK_IN_TYPE_MAP: dict[str, str] = {
    "Import":                 "101",
    "Local Purchase":         "102",
    "Manufacture/Assembling": "103",
    "Opening Stock":          "104",
}


# ─── Whitelisted entry point ───────────────────────────────────────────────────

@frappe.whitelist()
def upload_stock_in(stock_entry_name: str, company: str) -> dict:
    """
    Submit a Stock Entry to EFRIS as a T131 Goods Stock Maintain record.

    Reads the URA tab fields (efris_stock_in_type, efris_supplier_name,
    efris_supplier_tin, efris_reference_no) set by the user before submitting.

    On success:
      - efris_ura_remark ← URA response message
      - efris_reference_no ← kept as-is (user's input invoice ref sent to URA)
    """
    if not stock_entry_name:
        frappe.throw("Stock Entry name is required.")
    if not company:
        frappe.throw("Company is required.")

    doc = frappe.get_doc("Stock Entry", stock_entry_name)
    if doc.docstatus != 1:
        frappe.throw(
            "Stock Entry must be submitted (docstatus=1) before uploading to EFRIS.",
            title="Not Submitted",
        )

    payload = _build_t131_payload(doc, company)
    client  = EFRISClient(company=company)

    try:
        ensure_session(client, company)
    except Exception as exc:
        _log("Stock Entry", stock_entry_name, "T131", payload, {}, "Failed",
             f"Session load failed: {exc}")
        frappe.throw(str(exc), title="EFRIS Session Error")

    try:
        result = _call_t131(client, payload, stock_entry_name)

    except EFRISAPIError as exc:
        if exc.code not in SESSION_ERRORS:
            _log("Stock Entry", stock_entry_name, "T131", payload, {}, "Failed",
                 f"[{exc.code}] {exc.message}")
            frappe.throw(
                f"EFRIS T131 failed [<b>{exc.code}</b>]: {exc.message}",
                title="EFRIS T131 Error",
            )

        # Session stale — refresh via T104 and retry once (same pattern as goods.py)
        frappe.logger("efris").info(
            f"T131 [{exc.code}] {stock_entry_name} [{company}] — session stale, refreshing."
        )
        _log("Stock Entry", stock_entry_name, "T131", payload, {}, "Failed",
             f"Session stale [{exc.code}] — refreshing (T104) and retrying")

        try:
            refresh_session(client, company)
        except Exception as ref_exc:
            _log("Stock Entry", stock_entry_name, "T131", payload, {}, "Failed",
                 f"Session refresh failed: {ref_exc}")
            handle_session_error("T131", company, exc, "Stock Entry", stock_entry_name)

        try:
            result = _call_t131(client, payload, stock_entry_name)
        except EFRISAPIError as exc2:
            handle_session_error("T131", company, exc2, "Stock Entry", stock_entry_name)

    # ── Success: write URA response back to Stock Entry ───────────────────────
    ura_remark = _extract_remark(result)

    frappe.db.set_value(
        "Stock Entry", stock_entry_name,
        {"efris_ura_remark": ura_remark or "Stock In Submitted Successfully to URA EFRIS."},
        update_modified=False,
    )
    frappe.db.commit()

    _log("Stock Entry", stock_entry_name, "T131", payload, result, "Success")

    msg = (
        f"Good WOA! Stock Entry <b>{stock_entry_name}</b> submitted to "
        f"URA EFRIS successfully."
    )
    if ura_remark:
        msg += f"<br>URA Response: <b>{ura_remark}</b>"

    frappe.msgprint(msg, title="EFRIS T131 — Stock In Submitted", indicator="green")
    return {"status": "ok", "message": msg}


# ─── T131 HTTP call ───────────────────────────────────────────────────────────

def _call_t131(client: EFRISClient, payload: dict, doc_name: str) -> dict:
    """
    T131 spec: payload is a plain DICT (not wrapped in a list like T130).
    Encrypted, not signed (mirrors T114/T115 pattern in stock.py).
    """
    return client.call(
        "T131",
        payload=payload,
        encrypt=True,
        sign=False,
        doc_type="Stock Entry",
        doc_name=doc_name,
    )


# ─── Payload builder ──────────────────────────────────────────────────────────

def _build_t131_payload(doc, company: str) -> dict:
    """
    Build the T131 JSON payload from the Stock Entry document.

    goodsStockIn header:
      operationType  = "101" (always stock-in from this path)
      stockInType    = code derived from efris_stock_in_type label
      invoiceNo      = efris_reference_no or doc.name (max 20 chars)
      stockInDate    = posting_date
      supplierTin    = efris_supplier_tin  (omitted for Manufacture)
      supplierName   = efris_supplier_name (omitted for Manufacture)

    goodsStockInItem per line:
      commodityGoodsId = Item.efris_reference_no  (URA internal ID, if set)
      goodsCode        = item_code  (always sent — cannot both be empty)
      measureUnit      = T115 rateUnit code
      quantity         = qty
      unitPrice        = basic_rate (purchase price)
    """
    stock_in_label = (doc.get("efris_stock_in_type") or "").strip()
    stock_in_code  = STOCK_IN_TYPE_MAP.get(stock_in_label, "102")

    supplier_name  = (doc.get("efris_supplier_name") or "").strip()
    supplier_tin   = (doc.get("efris_supplier_tin")  or "").strip()
    reference_no   = (doc.get("efris_reference_no")  or doc.name or "").strip()
    posting_date   = str(doc.posting_date or "")

    # ── Header ────────────────────────────────────────────────────────────────
    header: dict = {
        "operationType":  "101",
        "stockInType":    stock_in_code,
        "invoiceNo":      reference_no[:20],
        "stockInDate":    posting_date,
        "isCheckBatchNo": "0",
        "rollBackIfError":"0",
        "goodsTypeCode":  "101",
    }

    # supplierName / supplierTin must be empty for Manufacture (code 103)
    if stock_in_code != "103":
        if supplier_name:
            header["supplierName"] = supplier_name[:100]
        if supplier_tin:
            header["supplierTin"] = supplier_tin[:50]

    # ── Items ─────────────────────────────────────────────────────────────────
    items = []
    for row in (doc.items or []):
        if not row.item_code:
            continue

        # Load URA internal goods ID from the Item master
        goods_id = frappe.db.get_value("Item", row.item_code, "efris_reference_no") or ""
        goods_id = str(goods_id).strip()

        uom_code = _uom_code(row.uom or row.stock_uom or "", company)

        line: dict = {
            "goodsCode":   str(row.item_code),
            "measureUnit": uom_code,
            "quantity":    str(float(row.qty or 0)),
            "unitPrice":   str(float(row.basic_rate or row.valuation_rate or 0)),
            "remarks":     "",
        }
        if goods_id:
            line["commodityGoodsId"] = goods_id

        items.append(line)

    if not items:
        frappe.throw(
            "Stock Entry has no item lines to submit to EFRIS.",
            title="No Items",
        )

    return {
        "goodsStockIn":     header,
        "goodsStockInItem": items,
    }


# ─── UOM helper ───────────────────────────────────────────────────────────────

def _uom_code(erpnext_uom: str, company: str) -> str:
    """
    Map ERPNext UOM → EFRIS T115 rateUnit code (max 3 chars).
    Lookup order: EFRIS UOM Mapping table → built-in dict → fallback "PP".
    """
    if not erpnext_uom:
        return "PP"

    db_code = (
        frappe.db.get_value(
            "EFRIS UOM Mapping",
            {"company": company, "code": erpnext_uom},
            "ura_uom",
        )
        or frappe.db.get_value(
            "EFRIS UOM Mapping",
            {"company": company, "uom_name": erpnext_uom},
            "ura_uom",
        )
    )
    if db_code:
        code = str(db_code).strip()
        if len(code) <= 3:
            return code

    return {
        "Nos": "PP",  "No": "PP",   "Pcs": "PP",  "Pieces": "PP",
        "Unit": "PP", "Units": "PP",
        "Kg": "KGM",  "Kgs": "KGM", "Kilogram": "KGM", "Kilograms": "KGM",
        "g": "GRM",   "Gram": "GRM","Grams": "GRM",
        "Ltr": "LTR", "Ltrs": "LTR","Litre": "LTR","Litres": "LTR","Liter": "LTR",
        "Ml": "MLT",  "mL": "MLT",  "Millilitre": "MLT",
        "Mtr": "MTR", "Meter": "MTR","Metre": "MTR",
        "Cm": "CMT",  "Centimeter": "CMT",
        "Box": "BX",  "Boxes": "BX",
        "Dozen": "DZ","Dz": "DZ",
        "Bundle": "BE",
        "Bag": "BG",  "Bags": "BG",
        "Bottle": "BO","Bottles": "BO",
        "Carton": "CT","Cartons": "CT",
        "Roll": "RL", "Rolls": "RL",
        "Pair": "PR", "Pairs": "PR",
        "Set": "SET",
        "Tonne": "TNE","Ton": "TNE","Tons": "TNE",
    }.get(erpnext_uom, "PP")


# ─── Response parser ──────────────────────────────────────────────────────────

def _extract_remark(result) -> str:
    """
    T131 response is a list of per-item objects.
    Collect any non-empty returnMessage entries into one summary string.
    If all items succeeded (returnMessage empty), returns "".
    """
    msgs = []
    items = result if isinstance(result, list) else [result] if isinstance(result, dict) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        rc  = str(item.get("returnCode")    or "").strip()
        rm  = str(item.get("returnMessage") or "").strip()
        gc  = str(item.get("goodsCode")     or "").strip()
        if rm:
            label = f"{gc}: " if gc else ""
            msgs.append(f"{label}{rm}" if rc == "00" or not rc else f"{label}[{rc}] {rm}")
    return " | ".join(msgs)


# ─── Log helper ───────────────────────────────────────────────────────────────

def _log(doc_type, doc_name, interface_code, request, response, status, error=""):
    log_efris(doc_type, doc_name, interface_code, request, response, status, error)
