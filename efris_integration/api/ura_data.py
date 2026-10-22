"""
Read-only URA data used by the reconciliation and import reports
(SAP B1 SalesReconciliation.cs, PurchaseReconciliation.cs, BulkSalesImport.cs,
BulkPurchaseImport.cs, StockReconciliation.cs).

  T106  invoice list   queryType "1" = sales (issued by us), "0" = purchases
                       (issued to us); invoiceKind is mandatory and single-
                       valued, so kinds 1 (invoice) and 2 (receipt) are both
                       fetched; pages of 99 (URA rejects 100+ with 1526)
  T108  invoice detail by FDN
  T127  goods catalogue (id, goodsCode, company-wide stock), pages of 99
  T128  stock of one URA goods id, per branch

List results are cached for 10 minutes so a report and the VAT summary don't
query URA again for the same period.
"""

from datetime import datetime

import frappe
from frappe.utils import flt

from efris_integration.api.base import EFRISClient
from efris_integration.api.session import call_with_session

PAGE_SIZE = 99
MAX_PAGES = 100
CACHE_SECONDS = 600
SALES, PURCHASES = "1", "0"


def ura_invoices(company: str, query_type: str, from_date, to_date) -> dict:
    """
    {"records": [normalised invoice dicts], "complete": bool, "total": int}.
    complete is False when URA reported more records than were returned.
    """
    key = f"efris_t106:{company}:{query_type}:{from_date}:{to_date}"
    cached = frappe.cache().get_value(key)
    if cached:
        return cached

    client = EFRISClient(company=company)
    records, total, seen = [], 0, set()
    for kind in ("1", "2"):
        page = 1
        while page <= MAX_PAGES:
            result = call_with_session(client, company, "T106", {
                "queryType":   query_type,
                "invoiceKind": kind,
                "startDate":   str(from_date),
                "endDate":     str(to_date),
                "pageNo":      str(page),
                "pageSize":    str(PAGE_SIZE),
            }, "EFRIS Reconciliation", f"T106 {query_type}/{kind}") or {}
            info = result.get("page") or {}
            total_size = int(flt(info.get("totalSize")))
            page_count = int(flt(info.get("pageCount"))) or 1
            if page == 1:
                total += total_size
            for rec in result.get("records") or []:
                inv = normalise_invoice(rec)
                if inv["fdn"] and inv["fdn"] not in seen:
                    seen.add(inv["fdn"])
                    records.append(inv)
                    if inv["original"]:
                        frappe.cache().hset(f"efris_ori:{company}", inv["fdn"], inv["original"])
            if page >= page_count:
                break
            page += 1

    out = {"records": records, "complete": len(records) >= total, "total": total}
    frappe.cache().set_value(key, out, expires_in_sec=CACHE_SECONDS)
    return out


def normalise_invoice(rec: dict) -> dict:
    return {
        "fdn":         str(rec.get("invoiceNo") or "").strip(),
        "id":          str(rec.get("id") or "").strip(),
        "reference":   str(rec.get("referenceNo") or "").strip(),
        "issued":      parse_ura_date(rec.get("issuedDate")),
        "buyer_tin":   str(rec.get("buyerTin") or "").strip(),
        "buyer_name":  str(rec.get("buyerLegalName") or rec.get("buyerBusinessName") or "").strip(),
        "seller_tin":  str(rec.get("sellerTin") or rec.get("sellerTinOrNin") or "").strip(),
        "seller_name": str(rec.get("sellerLegalOrBusinessName") or rec.get("businessName") or "").strip(),
        "currency":    str(rec.get("currency") or "UGX").strip(),
        "gross":       flt(rec.get("grossAmount")),
        "tax":         flt(rec.get("taxAmount")),
        "type":        str(rec.get("invoiceType") or "1").strip(),   # 1 invoice, 2 credit note, 4 debit note
        "original":    str(rec.get("oriInvoiceNo") or "").strip(),  # credit notes: the original's FDN
        "invalid":     str(rec.get("isInvalid") or "0").strip() == "1",
        "device":      str(rec.get("deviceNo") or "").strip(),
    }


def original_fdn(company: str, fdn: str) -> str:
    """Original invoice FDN of a credit note seen in a T106 list ("" if unknown)."""
    return frappe.cache().hget(f"efris_ori:{company}", fdn) or ""


def parse_ura_date(raw):
    """URA dates come as dd/MM/yyyy[ HH:mm:ss] (extendField format) or ISO."""
    raw = str(raw or "").strip()
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def invoice_detail(company: str, fdn: str) -> dict:
    client = EFRISClient(company=company)
    return call_with_session(client, company, "T108", {"invoiceNo": fdn}, "EFRIS Import", fdn) or {}


def goods_catalogue(company: str) -> dict:
    """{goodsCode: {"id", "stock", "unit", "name"}} for every good registered on URA."""
    key = f"efris_t127:{company}"
    cached = frappe.cache().get_value(key)
    if cached:
        return cached
    client = EFRISClient(company=company)
    goods, page = {}, 1
    while page <= MAX_PAGES:
        result = call_with_session(client, company, "T127",
                                   {"pageNo": str(page), "pageSize": str(PAGE_SIZE)},
                                   "EFRIS Stock Reconciliation", "T127") or {}
        for rec in result.get("records") or []:
            code = str(rec.get("goodsCode") or "").strip()
            if code:
                goods[code] = {"id": str(rec.get("id") or "").strip(), "stock": flt(rec.get("stock")),
                               "unit": rec.get("measureUnit") or "", "name": rec.get("goodsName") or ""}
        if page >= (int(flt((result.get("page") or {}).get("pageCount"))) or 1):
            break
        page += 1
    frappe.cache().set_value(key, goods, expires_in_sec=CACHE_SECONDS)
    return goods


def branch_stock(company: str, goods_id: str, branch_id: str = "") -> float:
    client = EFRISClient(company=company)
    payload = {"id": goods_id}
    if branch_id:
        payload["branchId"] = branch_id
    result = call_with_session(client, company, "T128", payload, "EFRIS Stock Reconciliation", goods_id) or {}
    return flt(result.get("stock"))


@frappe.whitelist()
def clear_cache(company: str) -> None:
    """Forget cached URA lists for a company (the reports' Refresh from URA button)."""
    frappe.cache().delete_keys(f"efris_t106:{company}:")
    frappe.cache().delete_value(f"efris_t127:{company}")
