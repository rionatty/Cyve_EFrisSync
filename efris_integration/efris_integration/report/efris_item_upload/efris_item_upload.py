"""
EFRIS Item Upload — register or update many items on URA at once
(SAP B1 BulkItemUpload.cs).

Mode:
  Register New       items not yet registered (Upload Type is not "Update")
  Update Registered  items already registered (Upload Type "Update")
"""

import frappe

from efris_integration.utils.helpers import map_uom


def execute(filters=None):
    f = frappe._dict(filters or {})
    return _columns(), _data(f)


def _columns():
    return [
        {"label": "Item", "fieldname": "name", "fieldtype": "Link", "options": "Item", "width": 160},
        {"label": "Item Name", "fieldname": "item_name", "fieldtype": "Data", "width": 220},
        {"label": "Item Group", "fieldname": "item_group", "fieldtype": "Link", "options": "Item Group", "width": 140},
        {"label": "Stock UOM", "fieldname": "stock_uom", "fieldtype": "Data", "width": 90},
        {"label": "URA UOM", "fieldname": "ura_uom", "fieldtype": "Data", "width": 80},
        {"label": "Commodity Category", "fieldname": "efris_goods_category_id", "fieldtype": "Data", "width": 140},
        {"label": "Upload Type", "fieldname": "efris_upload_type", "fieldtype": "Data", "width": 100},
        {"label": "URA Goods ID", "fieldname": "efris_reference_no", "fieldtype": "Data", "width": 140},
        {"label": "URA Remark", "fieldname": "efris_item_remark", "fieldtype": "Data", "width": 260},
    ]


def _data(f):
    conditions = ["disabled = 0", "is_sales_item = 1"]
    if (f.get("mode") or "Register New") == "Register New":
        conditions.append("ifnull(efris_upload_type, '') != 'Update'")
    else:
        conditions.append("efris_upload_type = 'Update'")
    if f.get("item_group"):
        conditions.append("item_group = %(item_group)s")
    if f.get("item_code"):
        conditions.append("(name like %(item_code)s or item_name like %(item_code)s)")

    rows = frappe.db.sql(
        f"""
        select name, item_name, item_group, stock_uom, efris_goods_category_id,
               efris_upload_type, efris_reference_no, efris_item_remark
        from `tabItem`
        where {' and '.join(conditions)}
        order by name
        limit 2000
        """,
        {"item_group": f.get("item_group"), "item_code": f"%{f.get('item_code')}%"},
        as_dict=True,
    )
    for r in rows:
        r["ura_uom"] = map_uom(r.stock_uom, f.get("company") or "")
    return rows
