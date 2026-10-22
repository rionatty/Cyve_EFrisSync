"""
EFRIS Credit Notes — bulk upload and approval check (SAP B1 BulkCNVerify.cs).

Show:
  To Upload           submitted credit notes not yet sent (or rejected / voided)
  Awaiting Approval   sent, URA has not approved yet
  All                 every credit note in the period
"""

import frappe


def execute(filters=None):
    filters = frappe._dict(filters or {})
    return _columns(), _data(filters)


def _columns():
    return [
        {"label": "Credit Note", "fieldname": "name", "fieldtype": "Link", "options": "Sales Invoice", "width": 160},
        {"label": "Date", "fieldname": "posting_date", "fieldtype": "Date", "width": 95},
        {"label": "Customer", "fieldname": "customer_name", "fieldtype": "Data", "width": 180},
        {"label": "Original Invoice", "fieldname": "return_against", "fieldtype": "Link",
         "options": "Sales Invoice", "width": 150},
        {"label": "Original FDN", "fieldname": "original_fdn", "fieldtype": "Data", "width": 130},
        {"label": "Amount", "fieldname": "grand_total", "fieldtype": "Currency", "options": "currency", "width": 120},
        {"label": "Application Ref", "fieldname": "efris_cn_reference_no", "fieldtype": "Data", "width": 140},
        {"label": "Approval", "fieldname": "efris_cn_approval", "fieldtype": "Data", "width": 110},
        {"label": "Credit Note FDN", "fieldname": "efris_fdn_number", "fieldtype": "Data", "width": 130},
        {"label": "URA Remark", "fieldname": "efris_remark", "fieldtype": "Data", "width": 260},
        {"label": "Currency", "fieldname": "currency", "fieldtype": "Data", "hidden": 1},
    ]


def _data(f):
    conditions = ["cn.docstatus = 1", "cn.is_return = 1",
                  "cn.posting_date between %(from_date)s and %(to_date)s"]
    if f.get("company"):
        conditions.append("cn.company = %(company)s")
    show = f.get("show") or "To Upload"
    if show == "To Upload":
        conditions.append(
            "(ifnull(cn.efris_cn_reference_no, '') = '' or cn.efris_cn_approval in ('Rejected', 'Voided'))"
        )
    elif show == "Awaiting Approval":
        conditions.append("ifnull(cn.efris_cn_reference_no, '') != '' and cn.efris_cn_approval = 'Pending'")

    return frappe.db.sql(
        f"""
        select cn.name, cn.posting_date, cn.customer_name, cn.return_against,
               ori.efris_fdn_number as original_fdn, cn.grand_total, cn.currency,
               cn.efris_cn_reference_no, cn.efris_cn_approval, cn.efris_fdn_number, cn.efris_remark
        from `tabSales Invoice` cn
        left join `tabSales Invoice` ori on ori.name = cn.return_against
        where {' and '.join(conditions)}
        order by cn.posting_date desc, cn.name desc
        """,
        {"from_date": f.get("from_date"), "to_date": f.get("to_date"), "company": f.get("company")},
        as_dict=True,
    )
