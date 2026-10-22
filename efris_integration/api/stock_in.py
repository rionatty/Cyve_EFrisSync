"""
T131 — Goods Stock Maintain (Stock In)

Mirrors SAP B1: GoodsReceipt.cs → StockIn section / T131 handler.

Payload (EFRIS spec pages 172-175):
  {
    "goodsStockIn": {
      "operationType":    "101",          Y  101=Increase / 102=Reduction
      "stockInType":      "102",          N  101=Import 102=Local 103=Manufacture 104=Opening
      "supplierTin":      "...",          N  empty if operationType=102 or stockInType=103
      "supplierName":     "...",          N  required if operationType=101 and not Manufacture
      "adjustType":       "",             N  required only if operationType=102
      "remarks":          "...",          N  required if operationType=102 and adjustType=104
      "stockInDate":      "yyyy-MM-dd",   N
      "productionBatchNo":"",             N  empty unless stockInType=103
      "productionDate":   "",             N  empty unless stockInType=103  (yyyy-MM-dd)
      "branchId":         "...",          N  from EFRIS Settings
      "invoiceNo":        "...",          N  max 20 chars
      "isCheckBatchNo":   "0",            N  default 0
      "rollBackIfError":  "0",            N  default 0
      "goodsTypeCode":    "101"           N  101=Goods 102=Fuel
    },
    "goodsStockInItem": [{
      "commodityGoodsId": "...",          N  URA internal ID from Item.efris_reference_no
      "goodsCode":        "...",          N  ERPNext item_code (cannot both be empty)
      "measureUnit":      "PP",           N  T115 rateUnit code
      "quantity":         "10",           Y  string, decimal allowed
      "unitPrice":        "5000",         Y  string, can be "0"
      "remarks":          ""              N
    }]
  }
"""

import frappe

from efris_integration.api.base import EFRISClient, EFRISAPIError
from efris_integration.api.session import (
    SESSION_ERRORS,
    ensure_session,
    refresh_session,
    handle_session_error,
)
from efris_integration.utils.helpers import (
    efris_num,
    get_efris_settings,
    get_mapped_ura_uom,
    log_efris,
    now_str,
    resolve_branch_for_doc,
)

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
    Submit a Stock Entry to EFRIS as a T131 Goods Stock Maintain (Stock In) record.

    Reads the URA tab fields the user has filled on the submitted Stock Entry:
      efris_stock_in_type  — Stock In Type (Import / Local Purchase / …)
      efris_supplier_name  — Supplier Name
      efris_supplier_tin   — Supplier TIN  (hidden; populated via dialog if used)
      efris_reference_no   — Reference No  (sent as invoiceNo to URA)

    On success writes URA's response back into efris_ura_remark.
    """
    if not stock_entry_name:
        frappe.throw("Stock Entry name is required.")
    if not company:
        frappe.throw("Company is required.")

    doc = frappe.get_doc("Stock Entry", stock_entry_name)
    if doc.docstatus != 1:
        frappe.throw(
            "Stock Entry must be submitted before uploading to EFRIS.",
            title="Not Submitted",
        )

    # ── Duplicate submission guard ────────────────────────────────────────────
    # IMPORTANT: efris_invoice_id and efris_status share the same fieldname
    # on Sales Invoice and Stock Entry.  When ERPNext creates a Stock Entry
    # from a Sales Invoice, the mapper copies matching custom fields — so a
    # new Stock Entry can have efris_invoice_id/efris_status already set from
    # the parent Sales Invoice before it has ever been submitted to T131.
    #
    # Therefore we use efris_ura_remark as the authoritative T131 indicator:
    #   • It exists ONLY on Stock Entry (no matching field on Sales Invoice)
    #   • It is only written by upload_stock_in() on success
    #   • It is never copied from any parent document
    #
    # A successful T131 submission requires BOTH:
    #   efris_ura_remark != ""   (T131 was actually called and responded)
    #   efris_status == "Submitted"  (response was success, not failure)
    t131_remark  = (doc.get("efris_ura_remark") or "").strip()
    efris_status = (doc.get("efris_status")     or "").strip()
    ura_ref      = (doc.get("efris_invoice_id") or "").strip()

    if t131_remark and efris_status == "Submitted":
        frappe.throw(
            f"Stock Entry <b>{stock_entry_name}</b> has already been submitted "
            f"to URA EFRIS.<br>"
            + (f"URA Reference No: <b>{ura_ref}</b><br>" if ura_ref else "")
            + "To re-submit, clear the <b>URA Reference No</b>, "
            "<b>EFRIS Status</b>, and <b>Remark</b> on the URA tab.",
            title="Already Submitted to EFRIS",
        )

    # ── Validate required URA fields ──────────────────────────────────────────
    stock_in_label = (doc.get("efris_stock_in_type") or "").strip()
    if not stock_in_label:
        frappe.throw(
            "Please set <b>Stock In Type</b> on the URA tab before uploading.",
            title="Stock In Type Required",
        )

    supplier_name = (doc.get("efris_supplier_name") or "").strip()
    stock_in_code = STOCK_IN_TYPE_MAP.get(stock_in_label, "102")
    if stock_in_code != "103" and not supplier_name:
        frappe.throw(
            "Please set <b>Supplier Name</b> on the URA tab.<br>"
            "(Supplier Name is optional only for Manufacture/Assembling.)",
            title="Supplier Name Required",
        )

    # ── Multi-branch: resolve URA Branch ID from the Stock Entry's warehouse ──
    # Must run BEFORE _build_t131_payload() so settings.branch_id is already
    # set to the warehouse branch when the payload header is built.
    settings = get_efris_settings(company)
    resolve_branch_for_doc(doc, settings)

    payload = _build_t131_payload(doc, company, settings=settings)
    client  = EFRISClient(company=company)
    # Sync resolved branch to client's own settings copy → correct globalInfo.brn
    client.settings.branch_id   = settings.branch_id
    client.settings.branch_name = settings.branch_name

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
                f"EFRIS stock-in failed [<b>{exc.code}</b>]: {exc.message}",
                title="EFRIS Error",
            )

        # Session stale — refresh via T104 and retry once
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

    # ── Success: persist URA response to Stock Entry ─────────────────────────
    ura_doc_id = _extract_ura_doc_id(result)   # URA-assigned T131 document number
    ura_remark = _extract_remark(result)

    updates = {
        "efris_status":      "Submitted",
        "efris_submitted_at": now_str(),
        "efris_ura_remark":  ura_remark or "Stock In Submitted Successfully to URA EFRIS.",
    }
    if ura_doc_id:
        # Store the URA document reference number so the duplicate guard works
        # and the user can cross-reference in the URA portal.
        updates["efris_invoice_id"] = ura_doc_id

    frappe.db.set_value("Stock Entry", stock_entry_name, updates, update_modified=False)
    frappe.db.commit()

    _log("Stock Entry", stock_entry_name, "T131", payload, result, "Success")

    msg = (
        f"Good WOA! Stock Entry <b>{stock_entry_name}</b> submitted to "
        f"URA EFRIS successfully."
    )
    if ura_doc_id:
        msg += f"<br>URA Reference No: <b>{ura_doc_id}</b>"
    if ura_remark:
        msg += f"<br>URA Response: <b>{ura_remark}</b>"

    frappe.msgprint(msg, title="Stock In Submitted to EFRIS", indicator="green")
    return {"status": "ok", "message": msg}


# ─── T131 HTTP call ───────────────────────────────────────────────────────────

def _call_t131(client: EFRISClient, payload: dict, doc_name: str) -> dict:
    """
    T131 spec: payload is a plain dict (not a list like T130).
    Encrypted; no RSA signing required (mirrors T114/T115 in stock.py).
    """
    return client.call(
        "T131",
        payload=payload,
        encrypt=True,
        sign=True,
        doc_type="Stock Entry",
        doc_name=doc_name,
    )


# ─── Payload builder ──────────────────────────────────────────────────────────

def _build_t131_payload(doc, company: str, settings=None) -> dict:
    """
    Build the complete T131 payload per EFRIS spec pages 172-175.

    SAP B1 field mapping:
      OIGN.U_StockInType   → goodsStockIn.stockInType
      OIGN.U_SupplierName  → goodsStockIn.supplierName
      OIGN.U_SupplierTIN   → goodsStockIn.supplierTin
      OIGN.DocNum          → goodsStockIn.invoiceNo
      OIGN.DocDate         → goodsStockIn.stockInDate
      Settings.BranchId    → goodsStockIn.branchId  (warehouse branch when multi-branch)
      IGN1.ItemCode        → goodsStockInItem.goodsCode
      IGN1.U_GoodsId       → goodsStockInItem.commodityGoodsId
      IGN1.UomCode         → goodsStockInItem.measureUnit
      IGN1.Quantity        → goodsStockInItem.quantity
      IGN1.Price           → goodsStockInItem.unitPrice

    `settings` may be passed in already-resolved (branch mutated by
    resolve_branch_for_doc) so the caller's branch resolution is honoured.
    When None, settings are loaded fresh from the DB.
    """
    if settings is None:
        settings = get_efris_settings(company)

    stock_in_label = (doc.get("efris_stock_in_type") or "").strip()
    stock_in_code  = STOCK_IN_TYPE_MAP.get(stock_in_label, "102")

    supplier_name  = (doc.get("efris_supplier_name") or "").strip()
    supplier_tin   = (doc.get("efris_supplier_tin")  or "").strip()
    reference_no   = (doc.get("efris_reference_no")  or "").strip()
    branch_id      = (settings.branch_id             or "").strip()
    posting_date   = str(doc.posting_date            or "")   # already yyyy-MM-dd

    # ── goodsStockIn header ───────────────────────────────────────────────────
    header: dict = {
        "operationType":   "101",           # always stock-in (increase) from this path
        "stockInType":     stock_in_code,   # 101-104
        "stockInDate":     posting_date,
        "isCheckBatchNo":  "0",
        "rollBackIfError": "0",
        "goodsTypeCode":   "101",           # 101=Goods (default)
    }

    # invoiceNo: OPTIONAL — EFRIS validates it against invoices already in their
    # system (error 2810 if it doesn't exist).  Only include when the user has
    # entered a Reference No that is a real EFRIS-registered invoice number.
    # SAP B1 leaves this empty unless the receipt is linked to a purchase invoice.
    if reference_no:
        header["invoiceNo"] = reference_no[:20]

    # branchId from EFRIS Settings (optional but always sent if configured)
    if branch_id:
        header["branchId"] = branch_id

    # supplierName / supplierTin: required for stock-in except Manufacture (103)
    # Per spec: "If stockInType=103, supplierTin must be empty"
    if stock_in_code != "103":
        if supplier_name:
            header["supplierName"] = supplier_name[:100]
        if supplier_tin:
            header["supplierTin"] = supplier_tin[:50]

    # productionBatchNo / productionDate: only for Manufacture/Assembling (103)
    # Per spec: "If stockInType != 103, productionBatchNo must be empty"
    if stock_in_code == "103":
        # Stock Entry doesn't have a production batch field by default;
        # use the Stock Entry name as a fallback batch reference.
        header["productionBatchNo"] = (doc.name or "")[:50]
        header["productionDate"]    = posting_date

    # ── goodsStockInItem lines ────────────────────────────────────────────────
    items = []
    for row in (doc.items or []):
        if not row.item_code:
            continue

        # URA internal goods ID from Item master (efris_reference_no)
        goods_id = str(
            frappe.db.get_value("Item", row.item_code, "efris_reference_no") or ""
        ).strip()

        uom_code   = _uom_code(row.uom or row.stock_uom or "", company)
        # efris_num(): rounds + strips trailing zeros to avoid URA error [665]
        # "integer max 12 chars, decimal max 8 chars" — str(float) can produce
        # 13+ dp from ERPNext Decimal field floating-point residue.
        qty        = efris_num(row.qty or 0, decimal_places=6)
        unit_price = efris_num(row.basic_rate or row.valuation_rate or 0, decimal_places=2)

        line: dict = {
            "goodsCode":   str(row.item_code),
            "measureUnit": uom_code,
            "quantity":    qty,
            "unitPrice":   unit_price,
            "remarks":     "",
        }

        # commodityGoodsId: optional — include when the item has been
        # registered on EFRIS (T130) and has a URA internal ID
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
    """Map ERPNext UOM → EFRIS T115 rateUnit code (max 3 chars)."""
    if not erpnext_uom:
        return "PP"

    db_code = get_mapped_ura_uom(erpnext_uom, company)
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
    T131 response: list of per-item objects each with returnCode / returnMessage.
    Returns a summary string of all non-empty messages.
    Empty string = all items succeeded with no message.
    """
    msgs = []
    items = (
        result if isinstance(result, list)
        else [result] if isinstance(result, dict)
        else []
    )
    for item in items:
        if not isinstance(item, dict):
            continue
        rc = str(item.get("returnCode")    or "").strip()
        rm = str(item.get("returnMessage") or "").strip()
        gc = str(item.get("goodsCode")     or "").strip()
        if rm:
            label = f"{gc}: " if gc else ""
            msgs.append(
                f"{label}{rm}" if (not rc or rc == "00")
                else f"{label}[{rc}] {rm}"
            )
    return " | ".join(msgs)


def _extract_ura_doc_id(result) -> str:
    """
    Extract the URA-assigned T131 document reference number from the response.

    T131 returns a list of per-item result objects.  The document-level
    reference number may appear under several field names depending on the
    EFRIS version:
        invoiceId       — used by most T-codes
        goodsStockInId  — T131-specific stock-in document ID
        stockInId       — alternate field name seen in some versions
        stockId         — shorter variant
        id              — generic fallback

    Returns the first non-empty, non-zero value found, or "" if not present.
    """
    candidates = (
        result if isinstance(result, list)
        else [result] if isinstance(result, dict)
        else []
    )
    for item in candidates:
        if not isinstance(item, dict):
            continue
        for key in ("invoiceId", "goodsStockInId", "stockInId", "stockId", "id"):
            val = str(item.get(key) or "").strip()
            if val and val not in ("0", ""):
                return val
    return ""


# ─── Log helper ───────────────────────────────────────────────────────────────

def _log(doc_type, doc_name, interface_code, request, response, status, error=""):
    log_efris(doc_type, doc_name, interface_code, request, response, status, error)
