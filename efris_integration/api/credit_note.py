"""
T110 — Submit Credit Note / Debit Note to EFRIS
T111 — Query Credit Notes

Mirrors: ARCreditNote.cs, T110.cs, T111.cs

In ERPNext a Credit Note is a Sales Invoice with is_return=1.
A Debit Note is a Purchase Invoice with is_return=1.
"""

from datetime import datetime

import frappe

from efris_integration.api.base import EFRISClient, EFRISAPIError
from efris_integration.api.invoice import (
    _basic_info,
    _build_goods_and_tax,
    _buyer_details,
    _pay_way,
    _seller_details,
)
from efris_integration.utils.helpers import get_customer_efris_fields


CREDIT_REASON_CODES = {
    "101": "Sales return",
    "102": "Cancellation",
    "103": "Pricing error",
    "104": "Duplicate invoice",
    "105": "Other",
}


@frappe.whitelist()
def submit_credit_note(invoice_name: str, reason_code: str = "101", reason: str = "") -> dict:
    """
    Submit a Sales Invoice marked as return (Credit Note) to EFRIS T110.
    Must link to the original EFRIS invoice ID.
    """
    doc = frappe.get_doc("Sales Invoice", invoice_name)

    if not doc.is_return:
        frappe.throw("This invoice is not flagged as a return/credit note.")
    if doc.docstatus != 1:
        frappe.throw("Credit note must be submitted before sending to EFRIS.")
    if doc.get("efris_invoice_id"):
        frappe.throw(f"Already submitted to EFRIS (ID: {doc.efris_invoice_id}).")

    # Retrieve original invoice EFRIS ID
    return_against = doc.return_against
    if not return_against:
        frappe.throw("Credit note must reference an original invoice (Return Against).")

    ori_invoice_id = frappe.db.get_value("Sales Invoice", return_against, "efris_invoice_id")
    if not ori_invoice_id:
        frappe.throw(
            f"Original invoice {return_against} has not been submitted to EFRIS. "
            "Submit it first before issuing a credit note."
        )

    ori_invoice_no = return_against
    settings = frappe.get_single("EFRIS Settings")
    client = EFRISClient()

    buyer = get_customer_efris_fields(doc.customer)
    goods_details, tax_details, summary = _build_goods_and_tax(doc, settings)

    issued_date = str(doc.posting_date) + " 00:00:00"

    payload = {
        "oriInvoiceId": ori_invoice_id,
        "oriInvoiceNo": ori_invoice_no,
        "reasonCode": reason_code,
        "reason": reason or CREDIT_REASON_CODES.get(reason_code, "Return"),
        "applicationTime": issued_date,
        "invoiceApplyCategoryCode": "101",
        "currency": doc.currency or "UGX",
        "contactName": doc.customer_name or "",
        "contactMobileNum": buyer.get("mobile_no") or "",
        "contactEmail": buyer.get("email_id") or "",
        "source": "103",
        "remarks": doc.get("remarks") or "",
        "sellersReferenceNo": doc.name,
        "sellerDetails": _seller_details(settings, doc),
        "buyerDetails": _buyer_details(doc, buyer),
        "basicInformation": _basic_info(doc, settings, invoice_type="2", ori_invoice_id=ori_invoice_id),
        "goodsDetails": goods_details,
        "taxDetails": tax_details,
        "summary": summary,
        "payWay": _pay_way(doc),
    }

    result = client.call("T110", payload, doc_type="Sales Invoice", doc_name=invoice_name)

    invoice_id = result.get("invoiceId") or result.get("referenceNo", "")
    antifake_code = result.get("antifakeCode", "")

    doc.db_set("efris_invoice_id", invoice_id, update_modified=False)
    doc.db_set("efris_antifake_code", antifake_code, update_modified=False)
    doc.db_set("efris_status", "Credit Note Submitted", update_modified=False)
    doc.db_set("efris_submitted_at", datetime.now(), update_modified=False)

    frappe.msgprint(f"Credit Note submitted to EFRIS. Reference: {invoice_id}")
    return {"invoice_id": invoice_id, "reference_no": invoice_id}


@frappe.whitelist()
def query_credit_notes(reference_no: str = "", from_date: str = "", to_date: str = "", page: int = 1) -> dict:
    client = EFRISClient()
    payload = {
        "referenceNo": reference_no,
        "startDate": from_date,
        "endDate": to_date,
        "pageNo": str(page),
        "pageSize": "20",
    }
    return client.call("T111", payload, doc_type="Sales Invoice", doc_name="CN Query")
