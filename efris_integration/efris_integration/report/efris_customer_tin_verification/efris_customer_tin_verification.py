"""
EFRIS Customer TIN Verification — check many customers' TINs with URA at once
(SAP B1 BulkBPVerify.cs).

Show:
  Not Verified   customers with no Business Type or no EFRIS Invoice Name yet
  All            every customer
"""

import frappe


def execute(filters=None):
    f = frappe._dict(filters or {})
    return _columns(), _data(f)


def _columns():
    return [
        {"label": "Customer", "fieldname": "name", "fieldtype": "Link", "options": "Customer", "width": 160},
        {"label": "Customer Name", "fieldname": "customer_name", "fieldtype": "Data", "width": 220},
        {"label": "Customer Group", "fieldname": "customer_group", "fieldtype": "Link",
         "options": "Customer Group", "width": 140},
        {"label": "TIN", "fieldname": "tax_id", "fieldtype": "Data", "width": 120},
        {"label": "Business Type", "fieldname": "efris_business_type", "fieldtype": "Data", "width": 110},
        {"label": "Name on URA", "fieldname": "efris_invoice_name", "fieldtype": "Data", "width": 220},
    ]


def _data(f):
    conditions = ["disabled = 0"]
    if (f.get("show") or "Not Verified") == "Not Verified":
        conditions.append("(ifnull(efris_business_type, '') = '' or ifnull(efris_invoice_name, '') = '')")
    if f.get("customer_group"):
        conditions.append("customer_group = %(customer_group)s")
    if f.get("with_tin_only"):
        conditions.append("ifnull(tax_id, '') != ''")
    return frappe.db.sql(
        f"""
        select name, customer_name, customer_group, tax_id, efris_business_type, efris_invoice_name
        from `tabCustomer`
        where {' and '.join(conditions)}
        order by name
        limit 2000
        """,
        {"customer_group": f.get("customer_group")},
        as_dict=True,
    )
