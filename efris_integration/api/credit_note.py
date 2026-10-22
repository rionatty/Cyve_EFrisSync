"""
Credit notes → URA EFRIS (a Sales Invoice with is_return=1).

Lifecycle (mirrors SAP B1 ARCreditNote.cs / BulkCNVerify.cs):

  1. Upload (T110)      Credit note application. Every line is copied from the
                        original invoice as URA holds it (T108 by original FDN):
                        unit, tax rate, categories, excise, deemed flags; amounts
                        are prorated by quantity. Saves the application reference.
  2. Check status (T111) 101 Approved → T108 on the credit note's own FDN gives
                        its Invoice ID, verification code and QR.
                        102 Pending · 103 Rejected · 104 Voided.
  3. Cancel             Pending application → T120 void.
                        Approved credit note → T114 cancellation application.
                        Runs on ERPNext cancel and from the form button.

Fields on the credit note: efris_cn_reference_no, efris_cn_approval,
efris_cn_approval_date, efris_cn_reason; after approval the usual
efris_fdn_number / efris_antifake_code / efris_invoice_id / efris_qr_* hold the
credit note's own fiscal details (so the print format shows them).
"""

import frappe

from efris_integration.api.base import EFRISClient
from efris_integration.api.invoice import _is_tax_included_in_rate, _trunc2, ura_validation_url
from efris_integration.api.session import call_with_session
from efris_integration.utils.helpers import (
    get_customer_efris_fields,
    get_efris_settings,
    now_str,
    resolve_branch_for_doc,
    safe_float,
)

# URA credit note reason codes
REASONS = {
    "101": "Return of products due to expiry or damage, etc.",
    "102": "Cancellation of the purchase",
    "103": "Invoice amount wrong",
    "104": "Partial or complete waive off",
    "105": "Others",
}

APPROVED, SUBMITTED, REJECTED, VOIDED = "101", "102", "103", "104"
APPROVAL_LABEL = {APPROVED: "Approved", SUBMITTED: "Pending", REJECTED: "Rejected", VOIDED: "Voided"}

# Copied from the original URA line onto the credit note line
COPY_FROM_ORIGINAL = (
    "item", "itemCode", "unitOfMeasure", "taxRate", "orderNumber", "goodsCategoryId",
    "goodsCategoryName", "categoryId", "categoryName", "exciseFlag", "exciseRate",
    "exciseRule", "exciseUnit", "exciseCurrency", "exciseRateName", "pack", "stick",
    "deemedFlag", "vatApplicableFlag",
)


# ─── 1. Upload (T110) ────────────────────────────────────────────────────────

@frappe.whitelist()
def submit_credit_note(invoice_name: str, reason_code: str = "102", reason: str = "",
                       show_message: bool = True) -> dict:
    doc = frappe.get_doc("Sales Invoice", invoice_name)
    if not doc.is_return:
        frappe.throw("This invoice is not a credit note (return).", title="EFRIS")
    if doc.docstatus != 1:
        frappe.throw("Submit the credit note before sending it to EFRIS.", title="EFRIS")
    if doc.get("efris_cn_reference_no") and doc.get("efris_cn_approval") not in ("Rejected", "Voided"):
        frappe.throw(
            f"Already sent to EFRIS (application {doc.efris_cn_reference_no}, "
            f"{doc.get('efris_cn_approval') or 'Pending'}). Use <b>Check EFRIS Status</b>.",
            title="Already Sent",
        )
    if not doc.get("efris_cn_reference_no") and doc.get("efris_fdn_number"):
        frappe.throw(f"Already on EFRIS as credit note {doc.efris_fdn_number}.", title="Already Sent")
    reason_code = str(reason_code or "102")
    if reason_code not in REASONS:
        frappe.throw(f"Unknown credit note reason code {reason_code}.", title="EFRIS")
    reason = (reason or "").strip()
    if reason_code == "105":
        reason = reason or (doc.get("remarks") or "").strip()
        if not reason or reason == "No Remarks":
            frappe.throw("Reason <b>Others</b> needs a description.", title="EFRIS")
    reason = reason or REASONS[reason_code]

    ori_name = doc.return_against
    if not ori_name:
        frappe.throw("The credit note must have <b>Return Against</b> set to the original invoice.", title="EFRIS")
    ori_id, ori_fdn = frappe.db.get_value(
        "Sales Invoice", ori_name, ["efris_invoice_id", "efris_fdn_number"]
    ) or (None, None)
    if not ori_id:
        frappe.throw(f"Original invoice {ori_name} has not been sent to EFRIS yet.", title="EFRIS")
    if not ori_fdn:
        frappe.throw(
            f"Original invoice {ori_name} has no FDN yet. Open it and use "
            "<b>EFRIS → Refresh from EFRIS</b>, then try again.",
            title="Original FDN Missing",
        )

    settings = get_efris_settings(doc.company)
    resolve_branch_for_doc(doc, settings)
    client = _client(doc.company, settings)

    original = _t108(client, doc, ori_fdn)
    goods, taxes, summary = _credit_lines(doc, original, reason)
    ori_basic = original.get("basicInformation") or {}
    buyer = get_customer_efris_fields(doc.customer)

    payload = {
        "oriInvoiceId":             ori_id,
        "oriInvoiceNo":             ori_fdn,
        "reasonCode":               reason_code,
        "reason":                   reason[:1024],
        "applicationTime":          now_str(),
        "invoiceApplyCategoryCode": "101",
        "currency":                 ori_basic.get("currency") or doc.currency or "UGX",
        "contactName":              doc.customer_name or "",
        "contactMobileNum":         buyer.get("mobile_no") or "",
        "contactEmail":             buyer.get("email_id") or "",
        "source":                   "103",
        "remarks":                  reason[:500],
        "sellersReferenceNo":       doc.name,
        "goodsDetails":             goods,
        "taxDetails":               taxes,
        "summary":                  summary,
        "payWay": [{
            "paymentMode":   "102",
            "paymentAmount": summary["grossAmount"],
            "orderNumber":   "1",
        }],
        "basicInformation": {
            "operator":            frappe.session.user,
            "invoiceKind":         "1",
            "invoiceIndustryCode": ori_basic.get("invoiceIndustryCode") or "101",
            "branchId":            settings.branch_id or "",
        },
    }

    result = call_with_session(client, doc.company, "T110", payload, "Sales Invoice", doc.name)
    reference = str((result or {}).get("referenceNo") or "").strip()
    if not reference:
        frappe.throw("URA accepted the request but returned no application reference.", title="EFRIS")

    _save(doc, {
        "efris_cn_reference_no":  reference,
        "efris_cn_approval":      "Pending",
        "efris_cn_approval_date": "",
        "efris_cn_reason":        f"{reason_code} {REASONS[reason_code]}"[:140],
        "efris_status":           "Credit Note Submitted",
        "efris_submitted_at":     now_str(),
        "efris_remark":           f"Credit note application {reference} sent; waiting for URA approval.",
    })
    if show_message:
        frappe.msgprint(
            f"Credit note sent to URA EFRIS.<br>Application reference: <b>{reference}</b><br>"
            "Use <b>Check EFRIS Status</b> once URA has approved it.",
            title="Credit Note Sent", indicator="green",
        )
    return {"status": "ok", "reference_no": reference}


def _credit_lines(doc, original: dict, reason: str) -> tuple:
    """Build negative T110 goodsDetails / taxDetails / summary from the original URA invoice."""
    ori_goods = original.get("goodsDetails") or []
    ori_taxes = original.get("taxDetails") or []
    included = _is_tax_included_in_rate(doc)

    goods, vat, excise = [], {}, {"net": 0.0, "tax": 0.0, "gross": 0.0}
    for line in doc.items:
        ori = next((g for g in ori_goods if str(g.get("itemCode")) == str(line.item_code)), None)
        if not ori:
            frappe.throw(
                f"Item <b>{line.item_code}</b> is not on the original invoice as URA holds it, "
                "so it can't be credited.",
                title="EFRIS",
            )
        qty = abs(safe_float(line.qty))
        ori_qty = abs(safe_float(ori.get("qty"))) or 1
        ori_price = safe_float(ori.get("unitPrice"))
        rate_str = str(ori.get("taxRate") or "")
        rate = safe_float(rate_str) if rate_str not in ("", "-") else 0.0
        deemed = str(ori.get("deemedFlag") or "") == "1"

        gross_price = safe_float(line.rate) if included else safe_float(line.rate) * (1 + rate)
        if round(gross_price) == round(ori_price):
            unit_price = ori_price
            if abs(qty - ori_qty) < 1e-9:
                total, tax = safe_float(ori.get("total")), safe_float(ori.get("tax"))
            else:
                total = _trunc2(qty * ori_price)
                tax = _trunc2(total * rate / (1 + rate)) if rate and not deemed else 0.0
        else:
            unit_price = round(gross_price, 4)
            total = _trunc2(qty * unit_price)
            tax = _trunc2(total * rate / (1 + rate)) if rate and not deemed else 0.0

        good = {k: ori[k] for k in COPY_FROM_ORIGINAL if ori.get(k) not in (None, "")}
        good.update({
            "qty":          _neg(qty),
            "unitPrice":    _num(unit_price),
            "total":        _neg(total),
            "tax":          _neg(tax),
            "discountFlag": "2",
        })

        if str(ori.get("exciseFlag") or "") == "1":
            ori_excise = safe_float(ori.get("exciseTax"))
            excise_tax = ori_excise if abs(qty - ori_qty) < 1e-9 else _trunc2(ori_excise * qty / ori_qty)
            good["exciseTax"] = _neg(excise_tax)
            base = round(total - tax, 2)
            excise["net"] += round(base - excise_tax, 2)
            excise["tax"] += excise_tax
            excise["gross"] += base

        goods.append(good)
        bucket = vat.setdefault(rate_str, {"net": 0.0, "tax": 0.0, "gross": 0.0})
        bucket["net"] += round(total - tax, 2)
        bucket["tax"] += tax
        bucket["gross"] += total

    taxes = []
    for rate_str, b in vat.items():
        ori_row = next((t for t in ori_taxes if str(t.get("taxRate")) == rate_str
                        and str(t.get("taxCategoryCode")) != "05"), {})
        net, tax = round(b["net"], 2), round(b["tax"], 2)
        taxes.append({
            "taxCategoryCode": ori_row.get("taxCategoryCode") or _category_code(rate_str),
            "netAmount":       _neg(net),
            "taxRate":         rate_str,
            "taxAmount":       _neg(tax),
            "grossAmount":     _neg(round(net + tax, 2)),
            "exciseUnit":      "",
            "exciseCurrency":  "",
            "taxRateName":     ori_row.get("taxRateName") or "",
        })
    if excise["tax"]:
        ori_row = next((t for t in ori_taxes if str(t.get("taxCategoryCode")) == "05"), {})
        net, tax = round(excise["net"], 2), round(excise["tax"], 2)
        taxes.append({
            "taxCategoryCode": "05",
            "netAmount":       _neg(net),
            "taxRate":         ori_row.get("taxRate") or "",
            "taxAmount":       _neg(tax),
            "grossAmount":     _neg(round(net + tax, 2)),
            "exciseUnit":      ori_row.get("exciseUnit") or "",
            "exciseCurrency":  ori_row.get("exciseCurrency") or "",
            "taxRateName":     ori_row.get("taxRateName") or "",
        })

    net = round(sum(b["net"] for b in vat.values()), 2)
    tax = round(sum(b["tax"] for b in vat.values()), 2)
    summary = {
        "netAmount":   _neg(net),
        "taxAmount":   _neg(tax),
        "grossAmount": _neg(round(net + tax, 2)),
        "itemCount":   str(len(goods)),
        "modeCode":    "1",
        "remarks":     reason[:500],
        "qrCode":      "",
    }
    return goods, taxes, summary


# ─── 2. Check approval (T111 → T108) ─────────────────────────────────────────

@frappe.whitelist()
def check_credit_note_status(invoice_name: str, show_message: bool = True) -> dict:
    doc = frappe.get_doc("Sales Invoice", invoice_name)
    reference = (doc.get("efris_cn_reference_no") or "").strip()
    if not reference:
        frappe.throw("This credit note has not been sent to EFRIS yet.", title="EFRIS")

    client = _client(doc.company)
    record = _application(client, doc, reference)
    status = str(record.get("approveStatus") or "").strip()
    label = APPROVAL_LABEL.get(status, f"Unknown ({status})")
    values = {"efris_cn_approval": label if status in APPROVAL_LABEL else doc.get("efris_cn_approval")}

    if status == APPROVED:
        cn_fdn = str(record.get("invoiceNo") or "").strip()
        detail = _t108(client, doc, cn_fdn) if cn_fdn else {}
        basic = detail.get("basicInformation") or {}
        antifake = str(basic.get("antifakeCode") or "").strip()
        url = ura_validation_url(doc.company, cn_fdn, antifake) if cn_fdn and antifake else ""
        values.update({
            "efris_fdn_number":       cn_fdn,
            "efris_antifake_code":    antifake,
            "efris_invoice_id":       str(basic.get("invoiceId") or "").strip(),
            "efris_qr_code":          url,
            "efris_qr_path":          url,
            "efris_cn_approval_date": str(basic.get("issuedDate") or record.get("applicationTime") or ""),
            "efris_status":           "Submitted",
            "efris_remark":           f"Credit note approved by URA. FDN {cn_fdn}.",
        })
    elif status == SUBMITTED:
        values["efris_remark"] = f"Application {reference} is still waiting for URA approval."
    elif status in (REJECTED, VOIDED):
        values.update({
            "efris_status": "Failed",
            "efris_remark": f"URA {label.lower()} application {reference}. You can send the credit note again.",
        })

    _save(doc, values)
    if show_message:
        frappe.msgprint(values.get("efris_remark") or f"Status: {label}", title=f"Credit Note: {label}",
                        indicator={"Approved": "green", "Pending": "orange"}.get(label, "red"))
    return {"status": label, "fdn": values.get("efris_fdn_number", "")}


# ─── 3. Cancel (T120 void / T114 cancel) ─────────────────────────────────────

@frappe.whitelist()
def cancel_credit_note_in_efris(invoice_name: str) -> dict:
    doc = frappe.get_doc("Sales Invoice", invoice_name)
    reference = (doc.get("efris_cn_reference_no") or "").strip()
    if not reference:
        frappe.throw("This credit note was never sent to EFRIS, so there is nothing to cancel there.",
                     title="EFRIS")
    client = _client(doc.company)

    if doc.get("efris_cn_approval") != "Approved":
        record = _application(client, doc, reference)
        status = str(record.get("approveStatus") or "").strip()
        if status == SUBMITTED:
            call_with_session(client, doc.company, "T120",
                              {"businessKey": str(record.get("id") or ""), "referenceNo": reference},
                              "Sales Invoice", doc.name)
            return _cancel_done(doc, "Voided", f"Credit note application {reference} voided in EFRIS.")
        if status == VOIDED:
            return _cancel_done(doc, "Voided", f"Application {reference} was already voided in EFRIS.")
        if status == REJECTED:
            return _cancel_done(doc, "Rejected", f"URA rejected application {reference}; nothing to cancel.")
        if status != APPROVED:
            frappe.throw(f"Unexpected EFRIS approval status '{status}' for {reference}.", title="EFRIS")
        if not doc.get("efris_fdn_number"):
            check_credit_note_status(doc.name, show_message=False)
            doc.reload()

    cn_fdn = (doc.get("efris_fdn_number") or "").strip()
    ori_id = frappe.db.get_value("Sales Invoice", doc.return_against, "efris_invoice_id") or ""
    if not (cn_fdn and ori_id):
        frappe.throw("The credit note FDN or the original Invoice ID is missing; run "
                     "<b>Check EFRIS Status</b> first.", title="EFRIS")
    result = call_with_session(client, doc.company, "T114", {
        "oriInvoiceId":             ori_id,
        "invoiceNo":                cn_fdn,
        "reason":                   "Credit note cancelled",
        "reasonCode":               "102",
        "invoiceApplyCategoryCode": "104",
    }, "Sales Invoice", doc.name)
    ref = str((result or {}).get("referenceNo") or "").strip()
    return _cancel_done(doc, "Cancel Requested",
                        f"Cancellation of credit note {cn_fdn} sent to URA"
                        + (f" (reference {ref})." if ref else "."))


def _cancel_done(doc, approval: str, remark: str) -> dict:
    _save(doc, {"efris_cn_approval": approval, "efris_remark": remark})
    frappe.msgprint(remark, title="EFRIS", indicator="green")
    return {"status": approval, "message": remark}


# ─── Bulk (used by the EFRIS Credit Notes report) ────────────────────────────

@frappe.whitelist()
def bulk_submit_credit_notes(names, reason_code: str = "102", reason: str = "") -> dict:
    return _bulk(names, lambda n: submit_credit_note(n, reason_code, reason, show_message=False))


@frappe.whitelist()
def bulk_check_credit_notes(names) -> dict:
    return _bulk(names, lambda n: check_credit_note_status(n, show_message=False))


def _bulk(names, fn) -> dict:
    names = frappe.parse_json(names) if isinstance(names, str) else names
    done, failed = [], []
    for name in names or []:
        try:
            result = fn(name)
            done.append({"name": name, "result": result})
            frappe.db.commit()
        except Exception as exc:
            # nothing is saved on the credit note before URA accepts, so no rollback
            # (that would also drop the EFRIS Log entry of the failed call)
            frappe.clear_messages()
            failed.append({"name": name, "error": frappe.utils.strip_html(str(exc))[:300]})
    return {"done": done, "failed": failed}


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _client(company: str, settings=None) -> EFRISClient:
    client = EFRISClient(company=company)
    if settings is not None:
        client.settings.branch_id = settings.branch_id
        client.settings.branch_name = settings.branch_name
    return client


def _t108(client, doc, fdn: str) -> dict:
    result = call_with_session(client, doc.company, "T108", {"invoiceNo": fdn}, "Sales Invoice", doc.name)
    if not (result or {}).get("goodsDetails") and not (result or {}).get("basicInformation"):
        frappe.throw(f"URA returned no details for FDN {fdn}.", title="EFRIS")
    return result


def _application(client, doc, reference: str) -> dict:
    """The T111 record for this credit note application."""
    ori_fdn = frappe.db.get_value("Sales Invoice", doc.return_against, "efris_fdn_number") or ""
    result = call_with_session(client, doc.company, "T111", {
        "referenceNo": reference,
        "oriInvoiceNo": ori_fdn,
        "queryType": "1",
        "pageNo": "1",
        "pageSize": "10",
    }, "Sales Invoice", doc.name) or {}
    records = result.get("records") or []
    match = [r for r in records if str(r.get("referenceNo") or "").strip() == reference]
    if not (match or records):
        frappe.throw(f"URA has no credit note application with reference {reference}.", title="EFRIS")
    return (match or records)[-1]


def _save(doc, values: dict) -> None:
    frappe.db.set_value("Sales Invoice", doc.name, values, update_modified=False)
    for k, v in values.items():
        doc.set(k, v)


def _category_code(rate_str: str) -> str:
    return {"0.18": "01", "0": "02", "-": "03"}.get(rate_str, "01")


def _num(value: float) -> str:
    return f"{round(value, 4):.4f}".rstrip("0").rstrip(".") or "0"


def _neg(value: float) -> str:
    return _num(-abs(value)) if value else "0"
