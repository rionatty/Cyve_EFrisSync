import frappe


def execute(filters=None):
    filters = filters or {}

    columns = [
        {"label": "Timestamp",      "fieldname": "timestamp",       "fieldtype": "Datetime",  "width": 160},
        {"label": "Interface Code", "fieldname": "interface_code",  "fieldtype": "Data",      "width": 80},
        {"label": "Status",         "fieldname": "status",          "fieldtype": "Data",      "width": 80},
        {"label": "Document Type",  "fieldname": "document_type",   "fieldtype": "Data",      "width": 140},
        {"label": "Document",       "fieldname": "document_name",   "fieldtype": "Data",      "width": 180},
        {"label": "Error",          "fieldname": "error_message",   "fieldtype": "Small Text","width": 300},
    ]

    conds = ["1=1"]
    params = {}

    if filters.get("interface_code"):
        conds.append("interface_code = %(interface_code)s")
        params["interface_code"] = filters["interface_code"]
    if filters.get("status"):
        conds.append("status = %(status)s")
        params["status"] = filters["status"]
    if filters.get("from_date"):
        conds.append("DATE(timestamp) >= %(from_date)s")
        params["from_date"] = filters["from_date"]
    if filters.get("to_date"):
        conds.append("DATE(timestamp) <= %(to_date)s")
        params["to_date"] = filters["to_date"]

    data = frappe.db.sql(
        f"""
        SELECT timestamp, interface_code, status,
               document_type, document_name, error_message
        FROM `tabEFRIS Log`
        WHERE {" AND ".join(conds)}
        ORDER BY timestamp DESC
        LIMIT 500
        """,
        params,
        as_dict=True,
    )
    return columns, data
