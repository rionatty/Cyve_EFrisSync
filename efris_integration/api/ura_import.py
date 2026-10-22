"""
Import invoices from URA as ERPNext drafts (SAP B1 BulkSalesImport.cs +
SalesInvoiceMapping.cs, BulkPurchaseImport.cs + PurchaseInvoiceMapping.cs).

  Sales      invoices issued for this company outside ERPNext (URA portal,
             other devices) → draft Sales Invoices, already marked as
             fiscalised (FDN, verification code, QR) so they are never
             uploaded again
  Purchases  supplier invoices URA holds for this company's TIN → draft
             Purchase Invoices (Supplier Invoice No = FDN)

Lines come from T108. Each URA goods code is matched to an item by EFRIS Item
Mapping (party TIN first, then any party), then by an item with the same code.
URA prices are VAT-inclusive; the draft gets the VAT-exclusive rate and the
company's default tax template, for review before submitting.
Credit notes are not imported (create them from the original invoice).
"""

import frappe
from frappe.utils import flt

from efris_integration.api.bulk import run_bulk
from efris_integration.api.ura_data import invoice_detail, parse_ura_date


@frappe.whitelist()
def create_drafts(company: str, direction: str, fdns) -> dict:
    return run_bulk(fdns, lambda fdn: create_draft(company, direction, fdn))


def create_draft(company: str, direction: str, fdn: str) -> dict:
    sales = direction == "Sales"
    doctype = "Sales Invoice" if sales else "Purchase Invoice"
    existing = existing_fdns(doctype, [fdn])
    if existing:
        return {"skipped": f"already in ERPNext ({existing[fdn]})"}

    detail = invoice_detail(company, fdn)
    basic = detail.get("basicInformation") or {}
    if str(basic.get("invoiceType") or "1") != "1":
        return {"skipped": "credit/debit note: create it from the original invoice"}
    party_info = detail.get("buyerDetails" if sales else "sellerDetails") or {}
    tin = str(party_info.get("buyerTin" if sales else "tin") or "").strip()
    name = str(party_info.get("buyerLegalName" if sales else "legalName") or "").strip()
    party = find_party("Customer" if sales else "Supplier", tin, name)
    if not party:
        frappe.throw(f"No {'customer' if sales else 'supplier'} with TIN {tin or '-'} or name '{name}'. "
                     "Create it (or set its Tax ID) first.")

    lines, unmapped = [], []
    for g in detail.get("goodsDetails") or []:
        total, tax = flt(g.get("total")), flt(g.get("tax"))
        if str(g.get("discountFlag") or "") == "0" and lines:
            # discount line: reduce the item line it belongs to
            lines[-1]["amount"] += total - tax
            continue
        code = str(g.get("itemCode") or "").strip()
        item = find_item(company, direction, tin, code)
        if not item:
            unmapped.append(f"{code} ({g.get('item') or ''})")
            continue
        lines.append({"item_code": item, "qty": flt(g.get("qty")) or 1, "amount": total - tax,
                      "description": g.get("item") or code})
    if unmapped:
        frappe.throw("Map these URA goods codes in <b>EFRIS Item Mapping</b>: " + ", ".join(unmapped))
    if not lines:
        frappe.throw("URA returned no goods lines for this invoice.")

    issued = parse_ura_date(basic.get("issuedDate")) or frappe.utils.today()
    doc = frappe.new_doc(doctype)
    doc.company = company
    doc.currency = basic.get("currency") or None
    doc.posting_date = issued
    doc.set_posting_time = 1
    if sales:
        doc.customer = party
        doc.due_date = issued
    else:
        doc.supplier = party
        doc.bill_no = fdn
        doc.bill_date = issued
    for l in lines:
        doc.append("items", {"item_code": l["item_code"], "qty": l["qty"],
                             "rate": flt(l["amount"] / l["qty"], 4), "description": l["description"]})
    doc.set_missing_values()
    _apply_default_taxes(doc)

    doc.efris_fdn_number = fdn
    doc.efris_invoice_id = str(basic.get("invoiceId") or "")
    if sales:
        from efris_integration.api.invoice import ura_validation_url
        antifake = str(basic.get("antifakeCode") or "")
        url = ura_validation_url(company, fdn, antifake) if antifake else ""
        doc.update({"efris_antifake_code": antifake, "efris_qr_code": url, "efris_qr_path": url,
                    "efris_fiscal_date": str(basic.get("issuedDate") or ""), "efris_status": "Submitted",
                    "efris_remark": "Imported from URA EFRIS."})
    doc.insert()
    return {"name": doc.name}


def _apply_default_taxes(doc):
    template_doctype = ("Sales Taxes and Charges Template" if doc.doctype == "Sales Invoice"
                        else "Purchase Taxes and Charges Template")
    if doc.get("taxes_and_charges") or doc.get("taxes"):
        return
    template = frappe.db.get_value(template_doctype, {"company": doc.company, "is_default": 1, "disabled": 0})
    if template:
        from erpnext.controllers.accounts_controller import get_taxes_and_charges
        doc.taxes_and_charges = template
        for row in get_taxes_and_charges(template_doctype, template) or []:
            doc.append("taxes", row)


def find_party(doctype: str, tin: str, name: str):
    if tin:
        party = frappe.db.get_value(doctype, {"tax_id": tin, "disabled": 0})
        if party:
            return party
    if name:
        field = "customer_name" if doctype == "Customer" else "supplier_name"
        party = frappe.db.get_value(doctype, {field: name, "disabled": 0})
        if not party and doctype == "Customer":
            party = frappe.db.get_value(doctype, {"efris_invoice_name": name, "disabled": 0})
        return party
    return None


def find_item(company: str, direction: str, tin: str, code: str):
    if not code:
        return None
    for party_tin in ([tin] if tin else []) + [""]:
        filters = {"company": company, "direction": direction, "ura_goods_code": code}
        filters["party_tin"] = party_tin if party_tin else ["is", "not set"]
        item = frappe.db.get_value("EFRIS Item Mapping", filters, "item_code")
        if item:
            return item
    return code if frappe.db.exists("Item", code) else None


def existing_fdns(doctype: str, fdns: list) -> dict:
    """{fdn: document name} for FDNs already in ERPNext (FDN field, or Supplier Invoice No)."""
    if not fdns:
        return {}
    found = {r.efris_fdn_number: r.name for r in frappe.get_all(
        doctype, filters={"efris_fdn_number": ["in", fdns], "docstatus": ["<", 2]},
        fields=["name", "efris_fdn_number"])}
    if doctype == "Purchase Invoice":
        for r in frappe.get_all(doctype, filters={"bill_no": ["in", fdns], "docstatus": ["<", 2]},
                                fields=["name", "bill_no"]):
            found.setdefault(r.bill_no, r.name)
    return found
