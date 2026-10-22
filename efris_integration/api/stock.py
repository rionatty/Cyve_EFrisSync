"""
Stock movements → URA EFRIS.

Every stock change goes through two URA interfaces (as in the SAP B1 add-on):
  T131  Goods Stock Maintain   operationType 101 = stock in, 102 = stock out
  T139  Goods Stock Transfer   between two URA branches of the same taxpayer

ERPNext document → what is sent
──────────────────────────────────────────────────────────────────────────────
Stock Entry  Material Receipt            T131 in   (Stock In Type + Supplier from URA tab)
             Material Issue,
             Send to Subcontractor       T131 out  (Adjust Type + Reason from URA tab)
             Manufacture, Repack         T131 in   finished / scrap lines, type 103
                                           (if "Receipt from Production" is on)
                                         T131 out  consumed lines, adjust "Raw Materials"
                                           (if "Issue from Production" is on)
             Material Transfer           T139 per source → destination branch pair
             Material Transfer
               for Manufacture           nothing (internal move; consumption is
                                           reported on the Manufacture entry)
Purchase Receipt                         T131 in   (Stock-In Type, supplier name + TIN)
Purchase Receipt (return)                T131 out  (adjust "Others", reason = return)
Stock Reconciliation  Opening Stock      T131 in   type 104
                      count difference   T131 in   (+) type 102 / out (-) adjust "Others"
──────────────────────────────────────────────────────────────────────────────

Quantities and prices are sent in the item's stock UOM.
Lines are grouped by URA branch (multi-branch) and sent one call per group,
with rollBackIfError=1 so each call is all-or-nothing. Each successful part is
recorded on the document (efris_invoice_id, e.g. "IN: 1234 | OUT: OK"), so a
retry after a partial failure only resends the parts that failed.

Mirrors: GoodsReceipt.cs, GoodsIssue.cs, GRPO.cs, GoodsReturn.cs,
         IssueFromProduction.cs, ReceiptFromProduction.cs, InventoryPosting.cs,
         InventoryTransfer.cs, Models/T131.cs, Models/T139.cs
"""

import frappe

from efris_integration.api.base import EFRISClient, EFRISAPIError
from efris_integration.api.session import call_with_session
from efris_integration.utils.helpers import (
    efris_num,
    get_efris_settings,
    get_supplier_efris_fields,
    map_uom,
    now_str,
    safe_float,
)

STOCK_IN = "101"
STOCK_OUT = "102"

# URA tab labels → URA codes
STOCK_IN_TYPES = {
    "Import":                 "101",
    "Local Purchase":         "102",
    "Manufacture/Assembling": "103",
    "Opening Stock":          "104",
}
ADJUST_TYPES = {
    "Expired Goods":  "101",
    "Damaged Goods":  "102",
    "Personal Uses":  "103",
    "Others":         "104",
    "Raw Materials":  "105",
}
TRANSFER_TYPES = {
    "Out of Stock Adjust": "101",
    "Error Adjust":        "102",
    "Others":              "103",
}

ADJUST_OTHERS = "104"
ADJUST_RAW_MATERIALS = "105"
STOCK_IN_LOCAL = "102"
STOCK_IN_MANUFACTURE = "103"
STOCK_IN_OPENING = "104"

REMARKS_MAX = 1000   # URA limit is 1024 bytes


# ─── Whitelisted entry points ─────────────────────────────────────────────────

@frappe.whitelist()
def submit_stock_entry(stock_entry_name: str) -> dict:
    doc = _get_submitted("Stock Entry", stock_entry_name)
    settings = get_efris_settings(doc.company)
    return _run(doc, _stock_entry_parts(doc, settings, manual=True))


@frappe.whitelist()
def submit_purchase_receipt(receipt_name: str) -> dict:
    doc = _get_submitted("Purchase Receipt", receipt_name)
    settings = get_efris_settings(doc.company)
    return _run(doc, _purchase_receipt_parts(doc, settings))


@frappe.whitelist()
def submit_stock_reconciliation(reconciliation_name: str) -> dict:
    doc = _get_submitted("Stock Reconciliation", reconciliation_name)
    settings = get_efris_settings(doc.company)
    return _run(doc, _stock_reconciliation_parts(doc, settings))


# ─── Called from on_submit hooks ──────────────────────────────────────────────

def auto_submit(doc) -> None:
    """
    Send a just-submitted stock document to URA. Never blocks the submit:
    problems are logged on the document and shown as an alert, and the user
    can retry from the EFRIS button.
    """
    try:
        settings = get_efris_settings(doc.company)
    except Exception:
        return  # EFRIS not configured for this company

    builders = {
        "Stock Entry":          lambda: _stock_entry_parts(doc, settings, manual=False),
        "Purchase Receipt":     lambda: _purchase_receipt_parts(doc, settings),
        "Stock Reconciliation": lambda: _stock_reconciliation_parts(doc, settings),
    }
    try:
        parts = builders[doc.doctype]()
        if parts:
            _run(doc, parts, show_message=False)
    except Exception as exc:
        frappe.log_error(frappe.get_traceback(), f"EFRIS {doc.doctype} {doc.name}")
        frappe.msgprint(
            f"{doc.doctype} {doc.name} was saved but not sent to EFRIS: {exc}<br>"
            "Fix the EFRIS tab if needed, then use <b>EFRIS → Upload to EFRIS</b>.",
            indicator="orange",
            alert=True,
        )


# ─── Part planning per document type ─────────────────────────────────────────
# A "part" is one URA call: (key, callable returning the URA reference or "").

def _stock_entry_parts(doc, settings, manual: bool) -> list:
    t = doc.stock_entry_type or doc.purpose
    marker = _marker(doc)

    if t == "Material Receipt":
        stock_in_type = _stock_in_type(doc)
        supplier = (doc.get("efris_supplier_name") or "").strip()
        if not stock_in_type:
            frappe.throw("Set <b>Stock In Type</b> on the URA tab first.", title="EFRIS")
        if stock_in_type != STOCK_IN_MANUFACTURE and not supplier:
            frappe.throw("Set <b>Supplier Name</b> on the URA tab first.", title="EFRIS")
        lines = _se_lines(doc, target=True)
        return _t131_parts(doc, settings, "IN", STOCK_IN, lines,
                           stock_in_type=stock_in_type,
                           supplier_name=supplier,
                           supplier_tin=(doc.get("efris_supplier_tin") or "").strip(),
                           invoice_no=(doc.get("efris_reference_no") or "").strip(),
                           remarks=marker)

    if t in ("Material Issue", "Send to Subcontractor"):
        adjust_type, remarks = _adjustment(doc, marker)
        return _t131_parts(doc, settings, "OUT", STOCK_OUT, _se_lines(doc, target=False),
                           adjust_type=adjust_type, remarks=remarks)

    if t in ("Manufacture", "Repack"):
        parts = []
        if settings.receipt_from_production:
            produced = [l for l in _se_lines(doc, target=True) if not l["source_line"]]
            parts += _t131_parts(doc, settings, "IN", STOCK_IN, produced,
                                 stock_in_type=STOCK_IN_MANUFACTURE, remarks=marker)
        if settings.issue_from_production:
            consumed = [l for l in _se_lines(doc, target=False) if not l["target_line"]]
            parts += _t131_parts(doc, settings, "OUT", STOCK_OUT, consumed,
                                 adjust_type=ADJUST_RAW_MATERIALS, remarks=marker)
        if not parts and manual:
            frappe.throw(
                "Turn on <b>Receipt from Production</b> and/or <b>Issue from Production</b> "
                "in EFRIS Settings to send production entries to URA.",
                title="EFRIS",
            )
        return parts

    if t == "Material Transfer":
        if not manual and not settings.stock_transfer:
            return []   # auto-send of transfers is switched off
        parts = _t139_parts(doc, settings)
        if not parts and manual:
            frappe.throw(
                "Nothing to send: URA only needs transfers between two different URA "
                "branches (set <b>URA Branch Id</b> on both warehouses and turn on "
                "<b>Multi-Branch</b> in EFRIS Settings).",
                title="EFRIS",
            )
        return parts

    if manual:
        frappe.throw(
            f"<b>{t}</b> entries are not sent to URA. Raw materials moved to a "
            "work-in-progress warehouse are reported as consumed on the "
            "<b>Manufacture</b> entry.",
            title="EFRIS",
        )
    return []


def _purchase_receipt_parts(doc, settings) -> list:
    marker = _marker(doc)
    lines = []
    for row in doc.items:
        if not row.item_code or not row.warehouse:
            continue
        cf = safe_float(row.conversion_factor) or 1
        lines.append(_line(row.item_code, row.stock_uom or row.uom, abs(safe_float(row.stock_qty)),
                           safe_float(row.base_rate) / cf, row.warehouse))

    if doc.is_return:
        reason = f"Returned to supplier {doc.supplier_name or doc.supplier}"
        if doc.return_against:
            reason += f" (against {doc.return_against})"
        return _t131_parts(doc, settings, "OUT", STOCK_OUT, lines,
                           adjust_type=ADJUST_OTHERS, remarks=_remarks(marker, reason))

    supplier_tin = (doc.get("efris_supplier_tin") or "").strip() \
        or (get_supplier_efris_fields(doc.supplier).get("tax_id") or "").strip()
    return _t131_parts(doc, settings, "IN", STOCK_IN, lines,
                       stock_in_type=_stock_in_code(doc.get("efris_stock_in_option")) or STOCK_IN_LOCAL,
                       supplier_name=doc.supplier_name or doc.supplier,
                       supplier_tin=supplier_tin,
                       remarks=marker)


def _stock_reconciliation_parts(doc, settings) -> list:
    marker = _marker(doc)
    opening = doc.purpose == "Opening Stock"
    ups, downs = [], []
    for row in doc.items:
        if not row.item_code:
            continue
        diff = safe_float(row.qty) - (0 if opening else safe_float(row.current_qty))
        if abs(diff) < 1e-9:
            continue
        uom = frappe.db.get_value("Item", row.item_code, "stock_uom")
        line = _line(row.item_code, uom, abs(diff), safe_float(row.valuation_rate), row.warehouse)
        (ups if diff > 0 else downs).append(line)

    own_name = settings.get("legal_name") or settings.get("company_name") or doc.company
    own_tin = settings.get("tin") or ""
    parts = _t131_parts(doc, settings, "IN", STOCK_IN, ups,
                        stock_in_type=STOCK_IN_OPENING if opening else STOCK_IN_LOCAL,
                        supplier_name=own_name, supplier_tin=own_tin, remarks=marker)
    parts += _t131_parts(doc, settings, "OUT", STOCK_OUT, downs,
                         adjust_type=ADJUST_OTHERS,
                         remarks=_remarks(marker, f"Stock count adjustment {doc.name}"))
    return parts


# ─── T131 (stock in / out) ───────────────────────────────────────────────────

def _t131_parts(doc, settings, key, operation, lines, *, stock_in_type="", supplier_name="",
                supplier_tin="", adjust_type="", invoice_no="", remarks="") -> list:
    parts = []
    groups = _group_by_branch(lines, settings)
    for (branch_id, branch_name), group in groups.items():
        part_key = key if len(groups) == 1 else f"{key} {branch_id}"
        payload = _t131_payload(doc, operation, group, branch_id,
                                stock_in_type=stock_in_type, supplier_name=supplier_name,
                                supplier_tin=supplier_tin, adjust_type=adjust_type,
                                invoice_no=invoice_no, remarks=remarks)
        branch = (branch_id, branch_name)
        parts.append((part_key, lambda p=payload, b=branch: _send(doc, "T131", p, b)))
    return parts


def _t131_payload(doc, operation, lines, branch_id, *, stock_in_type, supplier_name,
                  supplier_tin, adjust_type, invoice_no, remarks) -> dict:
    posting_date = str(doc.posting_date or "")
    header = {
        "operationType":   operation,
        "stockInDate":     posting_date,
        "isCheckBatchNo":  "0",
        "rollBackIfError": "1",
        "goodsTypeCode":   "101",
        "remarks":         (remarks or "")[:REMARKS_MAX],
    }
    if operation == STOCK_IN:
        header["stockInType"] = stock_in_type
        if stock_in_type == STOCK_IN_MANUFACTURE:
            header["productionBatchNo"] = (doc.name or "")[:50]
            header["productionDate"] = posting_date
        else:
            if supplier_name:
                header["supplierName"] = supplier_name[:100]
            if supplier_tin:
                header["supplierTin"] = supplier_tin[:50]
        if invoice_no:
            header["invoiceNo"] = invoice_no[:20]
    else:
        header["adjustType"] = adjust_type
        header["stockInType"] = ""
    if branch_id:
        header["branchId"] = branch_id

    items = []
    for l in lines:
        item = {
            "goodsCode":   l["item_code"],
            "measureUnit": map_uom(l["uom"], doc.company),
            "quantity":    efris_num(l["qty"], decimal_places=6),
            "unitPrice":   efris_num(l["price"], decimal_places=2),
            "remarks":     "",
        }
        goods_id = _goods_id(l["item_code"])
        if goods_id:
            item["commodityGoodsId"] = goods_id
        items.append(item)

    return {"goodsStockIn": header, "goodsStockInItem": items}


# ─── T139 (transfer between branches) ────────────────────────────────────────

def _t139_parts(doc, settings) -> list:
    if not settings.multi_branch:
        return []
    marker = _marker(doc)
    transfer_type = TRANSFER_TYPES.get((doc.get("efris_transfer_type") or "").strip(), "103")
    reason = (doc.get("efris_adjust_reason") or "").strip()
    remarks = _remarks(marker, reason)

    pairs: dict = {}
    for row in doc.items:
        if not (row.item_code and row.s_warehouse and row.t_warehouse):
            continue
        src = _branch(row.s_warehouse, settings)
        dst = _branch(row.t_warehouse, settings)
        if src[0] == dst[0]:
            continue   # same branch: internal move, nothing for URA
        pairs.setdefault((src, dst), []).append(row)

    parts = []
    for (src, dst), rows in pairs.items():
        items = []
        for row in rows:
            item = {
                "goodsCode":   row.item_code,
                "measureUnit": map_uom(row.stock_uom or row.uom, doc.company),
                "quantity":    efris_num(row.transfer_qty or row.qty, decimal_places=6),
                "remarks":     "",
            }
            goods_id = _goods_id(row.item_code)
            if goods_id:
                item["commodityGoodsId"] = goods_id
            items.append(item)
        payload = {
            "goodsStockTransfer": {
                "sourceBranchId":      src[0],
                "destinationBranchId": dst[0],
                "transferTypeCode":    transfer_type,
                "remarks":             remarks[:REMARKS_MAX],
                "rollBackIfError":     "1",
                "vehicleNo":           (doc.get("efris_vehicle_no") or "").strip(),
            },
            "goodsStockTransferItem": items,
        }
        key = f"TRANSFER {src[1] or src[0]} > {dst[1] or dst[0]}"
        parts.append((key, lambda p=payload, b=src: _send(doc, "T139", p, b)))
    return parts


# ─── Running parts and recording the result ──────────────────────────────────

def _run(doc, parts: list, show_message: bool = True) -> dict:
    if not parts:
        frappe.throw("No stock lines to send to EFRIS.", title="EFRIS")

    done = _parse_done(doc.get("efris_invoice_id") or "") if doc.get("efris_status") == "Failed" else {}
    sent = []
    for key, send in parts:
        if key in done:
            continue
        try:
            done[key] = send() or "OK"
            sent.append(key)
        except Exception as exc:
            msg = getattr(exc, "message", None) or str(exc)
            _record(doc, done, "Failed", f"{key} failed: {msg}")
            raise

    remark = "Sent to URA EFRIS: " + ", ".join(sent) if sent else "Already sent"
    _record(doc, done, "Submitted", remark)
    if show_message:
        frappe.msgprint(
            f"<b>{doc.name}</b> sent to URA EFRIS.<br>{_format_done(done)}",
            title="Stock Sent to EFRIS",
            indicator="green",
        )
    return {"status": "ok", "parts": done}


def _record(doc, done: dict, status: str, remark: str) -> None:
    values = {
        "efris_status":       status,
        "efris_invoice_id":   _format_done(done),
        "efris_submitted_at": now_str(),
    }
    if doc.meta.has_field("efris_ura_remark"):
        values["efris_ura_remark"] = remark[:1000]
    frappe.db.set_value(doc.doctype, doc.name, values, update_modified=False)
    for k, v in values.items():
        doc.set(k, v)


def _format_done(done: dict) -> str:
    return " | ".join(f"{k}: {v}" for k, v in done.items())


def _parse_done(text: str) -> dict:
    done = {}
    for chunk in text.split(" | "):
        if ": " in chunk:
            k, v = chunk.split(": ", 1)
            done[k.strip()] = v.strip()
    return done


# ─── URA call with one session retry ─────────────────────────────────────────

def _send(doc, code: str, payload: dict, branch: tuple) -> str:
    client = EFRISClient(company=doc.company)
    client.settings.branch_id, client.settings.branch_name = branch[0], branch[1]
    result = call_with_session(client, doc.company, code, payload, doc.doctype, doc.name)

    errors = _line_errors(result)
    if errors:
        raise EFRISAPIError("LINE", "; ".join(errors))
    return _reference(result)


def _line_errors(result) -> list:
    """T131/T139 return one result per line; any non-00 code means it was rejected."""
    rows = result if isinstance(result, list) else [result] if isinstance(result, dict) else []
    errors = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        code = str(r.get("returnCode") or "").strip()
        if code and code != "00":
            goods = str(r.get("goodsCode") or r.get("commodityGoodsId") or "").strip()
            errors.append(f"{goods + ': ' if goods else ''}[{code}] {r.get('returnMessage') or ''}".strip())
    return errors


def _reference(result) -> str:
    rows = result if isinstance(result, list) else [result] if isinstance(result, dict) else []
    for r in rows:
        if not isinstance(r, dict):
            continue
        for key in ("referenceNo", "invoiceId", "goodsStockInId", "stockInId", "stockId", "id"):
            val = str(r.get(key) or "").strip()
            if val and val != "0":
                return val
    return ""


# ─── Small helpers ───────────────────────────────────────────────────────────

def _get_submitted(doctype: str, name: str):
    doc = frappe.get_doc(doctype, name)
    if doc.docstatus != 1:
        frappe.throw(f"Submit the {doctype} before sending it to EFRIS.", title="EFRIS")
    if doc.get("efris_status") == "Submitted":
        frappe.throw(
            f"{doctype} <b>{name}</b> was already sent to URA EFRIS "
            f"({doc.get('efris_invoice_id') or 'no reference'}).",
            title="Already Sent to EFRIS",
        )
    return doc


def _line(item_code, uom, qty, price, warehouse, source_line=False, target_line=False) -> dict:
    return {"item_code": item_code, "uom": uom, "qty": qty, "price": price,
            "warehouse": warehouse, "source_line": source_line, "target_line": target_line}


def _se_lines(doc, target: bool) -> list:
    """Stock Entry lines on the receiving (target=True) or issuing side, in stock UOM."""
    lines = []
    for row in doc.items:
        wh = row.t_warehouse if target else row.s_warehouse
        if not (row.item_code and wh):
            continue
        lines.append(_line(row.item_code, row.stock_uom or row.uom,
                           safe_float(row.transfer_qty or row.qty),
                           safe_float(row.basic_rate or row.valuation_rate), wh,
                           source_line=bool(row.s_warehouse), target_line=bool(row.t_warehouse)))
    return lines


def _group_by_branch(lines: list, settings) -> dict:
    groups: dict = {}
    for l in lines:
        groups.setdefault(_branch(l["warehouse"], settings), []).append(l)
    return groups


def _branch(warehouse: str, settings) -> tuple:
    if not settings.multi_branch:
        return (settings.branch_id or "", settings.branch_name or "")
    wh = frappe.db.get_value("Warehouse", warehouse,
                             ["efris_branch_id", "efris_branch_name"], as_dict=True) or {}
    branch_id = (wh.get("efris_branch_id") or "").strip()
    if not branch_id:
        frappe.throw(
            f"Warehouse <b>{warehouse}</b> has no <b>URA Branch Id</b>. "
            "Set it on the warehouse's URA tab (Multi-Branch is on).",
            title="EFRIS Branch ID Missing",
        )
    return (branch_id, (wh.get("efris_branch_name") or "").strip())


def _stock_in_type(doc) -> str:
    return _stock_in_code(doc.get("efris_stock_in_type")) or _stock_in_code(doc.get("efris_stock_in_option"))


def _stock_in_code(value) -> str:
    """Stock-In Type as a URA code; accepts the label or (older records) the code itself."""
    value = (value or "").strip()
    return STOCK_IN_TYPES.get(value) or (value if value in STOCK_IN_TYPES.values() else "")


def _adjustment(doc, marker: str) -> tuple:
    label = (doc.get("efris_adjust_type") or "").strip()
    code = ADJUST_TYPES.get(label)
    if not code:
        frappe.throw("Set <b>Adjust Type</b> on the URA tab first.", title="EFRIS")
    reason = (doc.get("efris_adjust_reason") or "").strip()
    if code == ADJUST_OTHERS and not reason:
        frappe.throw("Adjust Type <b>Others</b> needs a <b>Reason</b> on the URA tab.", title="EFRIS")
    return code, _remarks(marker, reason)


def _marker(doc) -> str:
    return f"ERPNext {doc.doctype}#: {doc.name}"


def _remarks(marker: str, reason: str) -> str:
    return f"{marker} | {reason}" if reason else marker


def _goods_id(item_code: str) -> str:
    return str(frappe.db.get_value("Item", item_code, "efris_reference_no") or "").strip()
