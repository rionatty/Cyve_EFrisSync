"""
EFRIS Stock Reconciliation — ERPNext stock vs URA stock for one warehouse
(SAP B1 StockReconciliation.cs). URA stock is the company-wide T127 figure,
or the branch figure (T128) when Multi-Branch is on and the warehouse has a
URA Branch Id. Tick rows and "Adjust EFRIS Stock" to bring URA in line.
"""

import frappe
from frappe.utils import flt

from efris_integration.api.ura_data import branch_stock, goods_catalogue
from efris_integration.utils.helpers import get_efris_settings


def execute(filters=None):
    f = frappe._dict(filters or {})
    if not (f.company and f.warehouse):
        return _columns(), []
    return _columns(), _data(f)


def _columns():
    return [
        {"label": "Item", "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 160},
        {"label": "Item Name", "fieldname": "item_name", "fieldtype": "Data", "width": 220},
        {"label": "ERPNext Qty", "fieldname": "erp_qty", "fieldtype": "Float", "width": 110},
        {"label": "URA Qty", "fieldname": "ura_qty", "fieldtype": "Float", "width": 110},
        {"label": "Difference", "fieldname": "difference", "fieldtype": "Float", "width": 110},
        {"label": "Status", "fieldname": "status", "fieldtype": "Data", "width": 150},
    ]


def _data(f):
    settings = get_efris_settings(f.company)
    branch_id = ""
    if settings.multi_branch:
        branch_id = frappe.db.get_value("Warehouse", f.warehouse, "efris_branch_id") or ""
    catalogue = goods_catalogue(f.company)

    conditions = ["i.disabled = 0", "i.is_stock_item = 1"]
    if f.item_group:
        conditions.append("i.item_group = %(item_group)s")
    items = frappe.db.sql(
        f"""
        select i.name as item_code, i.item_name, ifnull(b.actual_qty, 0) as erp_qty
        from `tabItem` i
        left join `tabBin` b on b.item_code = i.name and b.warehouse = %(warehouse)s
        where {' and '.join(conditions)}
        order by i.name
        """,
        {"warehouse": f.warehouse, "item_group": f.item_group},
        as_dict=True,
    )

    rows = []
    for it in items:
        goods = catalogue.get(it.item_code)
        if not goods and not flt(it.erp_qty):
            continue   # neither side has anything
        if not goods:
            ura, status = None, "Not registered on URA"
        else:
            ura = branch_stock(f.company, goods["id"], branch_id) if branch_id else goods["stock"]
            status = "Matched" if abs(flt(it.erp_qty) - ura) < 1e-6 else "Different"
        diff = round(flt(it.erp_qty) - flt(ura), 6) if ura is not None else None
        if f.show == "Differences only" and status == "Matched":
            continue
        rows.append({**it, "ura_qty": ura, "difference": diff, "status": status})
    return rows
