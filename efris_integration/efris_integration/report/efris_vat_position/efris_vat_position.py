"""
EFRIS VAT Position — output VAT (sales) vs input VAT (purchases) for a period,
ERPNext beside URA, with the reconciled / unreconciled split
(SAP B1 VatPositionSummary.cs). Built from the two reconciliations.
"""

import frappe
from frappe.utils import flt

from efris_integration.api.reconcile import reconcile


def execute(filters=None):
    f = frappe._dict(filters or {})
    columns = [
        {"label": "Line", "fieldname": "line", "fieldtype": "Data", "width": 280},
        {"label": "ERPNext", "fieldname": "erp", "fieldtype": "Currency", "options": "currency", "width": 160},
        {"label": "URA", "fieldname": "ura", "fieldtype": "Currency", "options": "currency", "width": 160},
        {"label": "Difference", "fieldname": "difference", "fieldtype": "Currency", "options": "currency", "width": 160},
        {"label": "Currency", "fieldname": "currency", "fieldtype": "Data", "hidden": 1},
    ]
    if not (f.company and f.from_date and f.to_date):
        return columns, []

    s = reconcile(f.company, "Sales", f.from_date, f.to_date, f.tolerance)
    p = reconcile(f.company, "Purchases", f.from_date, f.to_date, f.tolerance)
    st, pt, cur = s["totals"], p["totals"], s["currency"]

    def line(label, erp, ura, bold=False):
        return {"line": f"<b>{label}</b>" if bold else label, "erp": flt(erp, 2), "ura": flt(ura, 2),
                "difference": flt(flt(erp) - flt(ura), 2), "currency": cur}

    rows = [
        line("Output VAT (sales)", st["erp_tax"], st["ura_tax"]),
        line("Input VAT (purchases)", pt["erp_tax"], pt["ura_tax"]),
        line("Net VAT position (output − input)", st["erp_tax"] - pt["erp_tax"], st["ura_tax"] - pt["ura_tax"], bold=True),
        line("Sales gross", st["erp_gross"], st["ura_gross"]),
        line("Purchases gross", pt["erp_gross"], pt["ura_gross"]),
        line("Output VAT reconciled", st["erp_tax_reconciled"], st["ura_tax_reconciled"]),
        line("Output VAT not reconciled", st["erp_tax"] - st["erp_tax_reconciled"], st["ura_tax"] - st["ura_tax_reconciled"]),
        line("Input VAT reconciled", pt["erp_tax_reconciled"], pt["ura_tax_reconciled"]),
        line("Input VAT not reconciled", pt["erp_tax"] - pt["erp_tax_reconciled"], pt["ura_tax"] - pt["ura_tax_reconciled"]),
    ]
    summary = [
        {"label": "Net VAT (ERPNext)", "value": rows[2]["erp"], "datatype": "Currency", "currency": cur},
        {"label": "Net VAT (URA)", "value": rows[2]["ura"], "datatype": "Currency", "currency": cur},
        {"label": "Difference", "value": rows[2]["difference"], "datatype": "Currency", "currency": cur,
         "indicator": "Green" if abs(rows[2]["difference"]) < 1 else "Red"},
    ]
    message = None
    if not (s["complete"] and p["complete"]):
        message = "URA did not return every invoice for this period; the figures are incomplete. Use a shorter range."
    return columns, rows, message, None, summary
