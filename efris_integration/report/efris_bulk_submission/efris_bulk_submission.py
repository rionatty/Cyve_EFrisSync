"""
EFRIS Bulk Submission Report.

Lists documents NOT yet submitted to EFRIS so the user can select and bulk-push.
Mirrors: BulkPosting.cs

Supported doctypes:
  - Sales Invoice  (normal + credit notes)
  - Purchase Receipt
  - Stock Entry    (Material Receipt, Material Issue, Manufacture, Material Transfer for Manufacture)
"""

import frappe


def execute(filters=None):
    filters = filters or {}
    doctype = filters.get("doctype") or "Sales Invoice"
    from_date = filters.get("from_date") or frappe.utils.add_months(frappe.utils.today(), -1)
    to_date = filters.get("to_date") or frappe.utils.today()

    columns = get_columns(doctype)
    data = get_data(doctype, from_date, to_date)
    return columns, data


def get_columns(doctype):
    base = [
        {"label": "Document", "fieldname": "name", "fieldtype": "Link",
         "options": doctype, "width": 180},
        {"label": "Date", "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
        {"label": "Amount", "fieldname": "grand_total", "fieldtype": "Currency", "width": 130},
        {"label": "EFRIS Status", "fieldname": "efris_status", "fieldtype": "Data", "width": 120},
    ]
    if doctype == "Sales Invoice":
        base.insert(2, {"label": "Customer", "fieldname": "customer", "fieldtype": "Link",
                        "options": "Customer", "width": 160})
    elif doctype in ("Purchase Receipt",):
        base.insert(2, {"label": "Supplier", "fieldname": "supplier", "fieldtype": "Link",
                        "options": "Supplier", "width": 160})
    elif doctype == "Stock Entry":
        base.insert(2, {"label": "Entry Type", "fieldname": "stock_entry_type",
                        "fieldtype": "Data", "width": 180})
    return base


def get_data(doctype, from_date, to_date):
    if doctype == "Sales Invoice":
        return frappe.db.sql("""
            SELECT name, posting_date, customer, grand_total,
                   efris_status, is_return
            FROM `tabSales Invoice`
            WHERE docstatus = 1
              AND (efris_invoice_id IS NULL OR efris_invoice_id = '')
              AND posting_date BETWEEN %(from_date)s AND %(to_date)s
            ORDER BY posting_date DESC
        """, {"from_date": from_date, "to_date": to_date}, as_dict=True)

    elif doctype == "Purchase Receipt":
        return frappe.db.sql("""
            SELECT name, posting_date, supplier, grand_total, efris_status
            FROM `tabPurchase Receipt`
            WHERE docstatus = 1
              AND (efris_invoice_id IS NULL OR efris_invoice_id = '')
              AND posting_date BETWEEN %(from_date)s AND %(to_date)s
            ORDER BY posting_date DESC
        """, {"from_date": from_date, "to_date": to_date}, as_dict=True)

    elif doctype == "Stock Entry":
        return frappe.db.sql("""
            SELECT name, posting_date, stock_entry_type,
                   total_amount AS grand_total, efris_status
            FROM `tabStock Entry`
            WHERE docstatus = 1
              AND stock_entry_type IN (
                  'Material Receipt','Material Issue','Manufacture',
                  'Material Transfer for Manufacture','Send to Subcontractor'
              )
              AND (efris_invoice_id IS NULL OR efris_invoice_id = '')
              AND posting_date BETWEEN %(from_date)s AND %(to_date)s
            ORDER BY posting_date DESC
        """, {"from_date": from_date, "to_date": to_date}, as_dict=True)

    return []
