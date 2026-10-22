"""
ERPNext ↔ URA invoice reconciliation (SAP B1 SalesReconciliation.cs,
PurchaseReconciliation.cs) and the VAT position built on it
(VatPositionSummary.cs).

Each URA invoice (T106) is matched to an ERPNext invoice, in this order:
  1. FDN            (ERPNext efris_fdn_number; purchases also Supplier Invoice No)
  2. Reference      (sales only: URA referenceNo = ERPNext name, set by our upload)
  3. Party + amount (same TIN, same sign, gross within the tolerance)
Statuses: Matched · Amount Difference · URA Only · ERPNext Only.
Amounts are compared in the company currency; ERPNext VAT excludes the excise
tax row (EFRIS Settings → Excise Duty Account), as URA's tax amount does.
"""

import frappe
from frappe.utils import flt

from efris_integration.api.ura_data import PURCHASES, SALES, ura_invoices

DEFAULT_TOLERANCE = 100.0


def reconcile(company: str, kind: str, from_date, to_date, tolerance=None) -> dict:
    """kind 'Sales' or 'Purchases'. Returns rows, totals and URA completeness."""
    tolerance = flt(tolerance) if tolerance not in (None, "") else DEFAULT_TOLERANCE
    sales = kind == "Sales"
    company_currency = frappe.get_cached_value("Company", company, "default_currency")
    ura = ura_invoices(company, SALES if sales else PURCHASES, from_date, to_date)
    ura_rows = [dict(r, base_gross=_to_base(r["gross"], r["currency"], company_currency, r["issued"] or to_date),
                     base_tax=_to_base(r["tax"], r["currency"], company_currency, r["issued"] or to_date),
                     party_tin=r["buyer_tin"] if sales else r["seller_tin"],
                     party_name=r["buyer_name"] if sales else r["seller_name"])
                for r in ura["records"] if not r["invalid"]]
    erp_rows = _erp_invoices(company, sales, from_date, to_date)

    by_fdn, by_name = {}, {}
    for e in erp_rows:
        for key in (e.fdn, e.bill_no):
            if key:
                by_fdn.setdefault(key, e)
        by_name[e.name] = e

    rows, used = [], set()

    def pair(u, e, method):
        used.add(e.name)
        u["matched"] = True
        diff = flt(e.base_gross - u["base_gross"], 2)
        status = "Matched" if abs(diff) <= tolerance else "Amount Difference"
        rows.append(_row(status, method, u, e, diff))

    for u in ura_rows:
        e = by_fdn.get(u["fdn"]) or (by_name.get(u["reference"]) if sales and u["reference"] else None)
        if e and e.name not in used:
            pair(u, e, "FDN" if by_fdn.get(u["fdn"]) is e else "Reference")
    for u in ura_rows:
        if u.get("matched") or not u["party_tin"]:
            continue
        for e in erp_rows:
            if (e.name not in used and e.tax_id == u["party_tin"]
                    and (e.base_gross < 0) == (u["base_gross"] < 0)
                    and abs(e.base_gross - u["base_gross"]) <= tolerance):
                pair(u, e, "Party + amount")
                break
    for u in ura_rows:
        if not u.get("matched"):
            rows.append(_row("URA Only", "", u, None, None))
    for e in erp_rows:
        if e.name not in used:
            rows.append(_row("ERPNext Only", "", None, e, None))

    order = {"Amount Difference": 0, "URA Only": 1, "ERPNext Only": 2, "Matched": 3}
    rows.sort(key=lambda r: (order[r["status"]], str(r.get("ura_date") or r.get("erp_date") or "")))
    return {"rows": rows, "totals": _totals(rows), "complete": ura["complete"],
            "ura_total": ura["total"], "ura_count": len(ura["records"]), "currency": company_currency}


def _row(status, method, u, e, diff) -> dict:
    return {
        "status": status, "matched_by": method,
        "fdn": u["fdn"] if u else (e.fdn or e.bill_no),
        "ura_date": u["issued"] if u else None,
        "ura_party": (u["party_name"] or u["party_tin"]) if u else "",
        "ura_gross": u["base_gross"] if u else None,
        "ura_tax": u["base_tax"] if u else None,
        "erp_doc": e.name if e else None,
        "erp_date": e.posting_date if e else None,
        "erp_party": e.party_name if e else "",
        "erp_gross": e.base_gross if e else None,
        "erp_tax": e.base_vat if e else None,
        "difference": diff,
    }


def _totals(rows) -> dict:
    t = {k: 0.0 for k in ("erp_gross", "erp_tax", "ura_gross", "ura_tax",
                          "erp_tax_reconciled", "ura_tax_reconciled")}
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        for k in ("erp_gross", "erp_tax", "ura_gross", "ura_tax"):
            t[k] += flt(r[k])
        if r["status"] == "Matched":
            t["erp_tax_reconciled"] += flt(r["erp_tax"])
            t["ura_tax_reconciled"] += flt(r["ura_tax"])
    t = {k: flt(v, 2) for k, v in t.items()}
    t["counts"] = counts
    return t


def _erp_invoices(company, sales, from_date, to_date) -> list:
    doctype = "Sales Invoice" if sales else "Purchase Invoice"
    party, party_doctype = ("customer", "Customer") if sales else ("supplier", "Supplier")
    party_name = "customer_name" if sales else "supplier_name"
    bill_no = "''" if sales else "inv.bill_no"
    rows = frappe.db.sql(
        f"""
        select inv.name, inv.posting_date, inv.{party} as party, inv.{party_name} as party_name,
               p.tax_id, ifnull(inv.efris_fdn_number, '') as fdn, {bill_no} as bill_no,
               inv.base_grand_total as base_gross, inv.base_total_taxes_and_charges as base_taxes
        from `tab{doctype}` inv
        left join `tab{party_doctype}` p on p.name = inv.{party}
        where inv.docstatus = 1 and inv.company = %(company)s
          and inv.posting_date between %(from_date)s and %(to_date)s
        """,
        {"company": company, "from_date": from_date, "to_date": to_date},
        as_dict=True,
    )
    excise_account = frappe.get_cached_value("EFRIS Settings", company, "excise_liability_account") \
        if frappe.db.exists("EFRIS Settings", company) else None
    excise = {}
    if excise_account and rows:
        tax_table = "Sales Taxes and Charges" if sales else "Purchase Taxes and Charges"
        for r in frappe.db.sql(
            f"""select parent, sum(base_tax_amount_after_discount_amount) as amount from `tab{tax_table}`
                where parenttype = %s and account_head = %s and parent in %s group by parent""",
            (doctype, excise_account, tuple(r.name for r in rows)), as_dict=True,
        ):
            excise[r.parent] = flt(r.amount)
    for r in rows:
        r.tax_id = (r.tax_id or "").strip()
        r.bill_no = (r.bill_no or "").strip()
        r.base_vat = flt(flt(r.base_taxes) - excise.get(r.name, 0.0), 2)
        r.base_gross = flt(r.base_gross, 2)
    return rows


def _to_base(amount, currency, company_currency, date) -> float:
    if not amount or not currency or currency == company_currency:
        return flt(amount, 2)
    from erpnext.setup.utils import get_exchange_rate
    return flt(flt(amount) * flt(get_exchange_rate(currency, company_currency, date) or 1), 2)


# ─── Shared report pieces ────────────────────────────────────────────────────

def report_columns(kind: str, currency: str) -> list:
    party = "Customer" if kind == "Sales" else "Supplier"
    doctype = "Sales Invoice" if kind == "Sales" else "Purchase Invoice"
    money = {"fieldtype": "Currency", "options": "currency", "width": 120}
    return [
        {"label": "Status", "fieldname": "status", "fieldtype": "Data", "width": 140},
        {"label": "FDN", "fieldname": "fdn", "fieldtype": "Data", "width": 135},
        {"label": "URA Date", "fieldname": "ura_date", "fieldtype": "Date", "width": 95},
        {"label": f"{party} (URA)", "fieldname": "ura_party", "fieldtype": "Data", "width": 180},
        dict(money, label="URA Gross", fieldname="ura_gross"),
        dict(money, label="URA Tax", fieldname="ura_tax", width=110),
        {"label": "ERPNext Invoice", "fieldname": "erp_doc", "fieldtype": "Link", "options": doctype, "width": 150},
        {"label": "ERPNext Date", "fieldname": "erp_date", "fieldtype": "Date", "width": 95},
        {"label": f"{party} (ERPNext)", "fieldname": "erp_party", "fieldtype": "Data", "width": 180},
        dict(money, label="ERPNext Gross", fieldname="erp_gross"),
        dict(money, label="ERPNext VAT", fieldname="erp_tax", width=110),
        dict(money, label="Difference", fieldname="difference", width=110),
        {"label": "Matched By", "fieldname": "matched_by", "fieldtype": "Data", "width": 120},
        {"label": "Currency", "fieldname": "currency", "fieldtype": "Data", "hidden": 1},
    ]


def run_report(kind: str, filters) -> tuple:
    f = frappe._dict(filters or {})
    if not (f.company and f.from_date and f.to_date):
        return report_columns(kind, ""), []
    res = reconcile(f.company, kind, f.from_date, f.to_date, f.tolerance)
    rows = [dict(r, currency=res["currency"]) for r in res["rows"]
            if not f.status or f.status == "All" or r["status"] == f.status]
    t, c = res["totals"], res["totals"]["counts"]
    summary = [
        {"label": "Matched", "value": c.get("Matched", 0), "indicator": "Green", "datatype": "Int"},
        {"label": "Amount Difference", "value": c.get("Amount Difference", 0), "indicator": "Red", "datatype": "Int"},
        {"label": "URA Only", "value": c.get("URA Only", 0), "indicator": "Orange", "datatype": "Int"},
        {"label": "ERPNext Only", "value": c.get("ERPNext Only", 0), "indicator": "Orange", "datatype": "Int"},
        {"label": "ERPNext VAT", "value": t["erp_tax"], "datatype": "Currency", "currency": res["currency"]},
        {"label": "URA Tax", "value": t["ura_tax"], "datatype": "Currency", "currency": res["currency"]},
    ]
    message = None
    if not res["complete"]:
        message = (f"URA reported {res['ura_total']} invoices but returned {res['ura_count']}; "
                   "results are incomplete. Use a shorter date range.")
    return report_columns(kind, res["currency"]), rows, message, None, summary
