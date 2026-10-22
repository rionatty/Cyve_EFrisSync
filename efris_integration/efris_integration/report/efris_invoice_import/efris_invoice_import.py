"""
EFRIS Invoice Import — URA invoices that are not in ERPNext yet
(SAP B1 BulkSalesImport.cs / BulkPurchaseImport.cs).

Direction:
  Sales      invoices issued for this company outside ERPNext
  Purchases  supplier invoices URA holds for this company
Tick rows and "Create Draft Invoices" (see api/ura_import.py).
"""

import frappe

from efris_integration.api.ura_data import PURCHASES, SALES, ura_invoices
from efris_integration.api.ura_import import existing_fdns, find_party


def execute(filters=None):
    f = frappe._dict(filters or {})
    if not (f.company and f.from_date and f.to_date):
        return _columns(f), []
    sales = (f.direction or "Sales") == "Sales"
    ura = ura_invoices(f.company, SALES if sales else PURCHASES, f.from_date, f.to_date)
    rows = _data(f, ura["records"], sales)
    message = None
    if not ura["complete"]:
        message = (f"URA reported {ura['total']} invoices but returned {len(ura['records'])}. "
                   "Use a shorter date range (a week at a time).")
    return _columns(f), rows, message


def _columns(f):
    party = "Customer" if (f.direction or "Sales") == "Sales" else "Supplier"
    return [
        {"label": "FDN", "fieldname": "name", "fieldtype": "Data", "width": 140},
        {"label": "Issued", "fieldname": "issued", "fieldtype": "Date", "width": 95},
        {"label": f"{party} TIN", "fieldname": "party_tin", "fieldtype": "Data", "width": 110},
        {"label": f"{party} (URA)", "fieldname": "party_name", "fieldtype": "Data", "width": 200},
        {"label": f"{party} (ERPNext)", "fieldname": "party", "fieldtype": "Link", "options": party, "width": 160},
        {"label": "Currency", "fieldname": "currency", "fieldtype": "Data", "width": 70},
        {"label": "Gross", "fieldname": "gross", "fieldtype": "Float", "precision": 2, "width": 120},
        {"label": "Tax", "fieldname": "tax", "fieldtype": "Float", "precision": 2, "width": 110},
        {"label": "Reference", "fieldname": "reference", "fieldtype": "Data", "width": 120},
        {"label": "Device", "fieldname": "device", "fieldtype": "Data", "width": 120},
    ]


def _data(f, records, sales):
    doctype = "Sales Invoice" if sales else "Purchase Invoice"
    invoices = [r for r in records if not r["invalid"] and r["type"] == "1"]
    known = existing_fdns(doctype, [r["fdn"] for r in invoices])
    if sales:
        # invoices this app uploaded carry the ERPNext name as reference
        refs = [r["reference"] for r in invoices if r["reference"]]
        ours = set(frappe.get_all("Sales Invoice", filters={"name": ["in", refs]}, pluck="name")) if refs else set()
    rows = []
    for r in invoices:
        if r["fdn"] in known or (sales and r["reference"] in ours):
            continue
        tin = r["buyer_tin"] if sales else r["seller_tin"]
        name = r["buyer_name"] if sales else r["seller_name"]
        rows.append({
            "name": r["fdn"], "issued": r["issued"], "party_tin": tin, "party_name": name,
            "party": find_party("Customer" if sales else "Supplier", tin, name),
            "currency": r["currency"], "gross": r["gross"], "tax": r["tax"],
            "reference": r["reference"], "device": r["device"],
        })
    return rows
