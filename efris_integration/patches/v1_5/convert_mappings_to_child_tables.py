"""
EFRIS Tax Mapping and EFRIS UOM Mapping used to be one record per mapping.
They are now one record per company (named after it) with the mappings in a
child table. This moves the old records into their company's table, then
drops the old columns so the patch is a no-op on re-run.

Runs post model sync: the child tables exist, and the old columns are still
on the parent table (Frappe never drops columns on sync).
"""

import frappe

LEGACY = {
    "EFRIS Tax Mapping": ["erpnext_tax_code", "ura_tax_code", "sales_order_tax"],
    "EFRIS UOM Mapping": ["code", "uom_name", "ura_uom"],
}


def execute():
    for doctype, cols in LEGACY.items():
        cols = [c for c in cols if frappe.db.has_column(doctype, c)]
        if not cols:
            continue

        col_sql = ", ".join(f"`{c}`" for c in cols)
        old_rows = frappe.db.sql(
            f"select name, company, {col_sql} from `tab{doctype}` "
            "where name != ifnull(company, '') order by creation",
            as_dict=True,
        )

        by_company = {}
        for row in old_rows:
            by_company.setdefault(row.company, []).append(row)

        for company, rows in by_company.items():
            # Old records are named by hash; free them before creating the
            # per-company record, which takes the company as its name.
            frappe.db.delete(doctype, {"name": ("in", [r.name for r in rows])})

            if not company or not frappe.db.exists("Company", company):
                frappe.log_error(
                    f"Dropped {len(rows)} {doctype} row(s) for missing company {company!r}: "
                    f"{[{c: r[c] for c in cols} for r in rows]}",
                    f"{doctype} migration",
                )
                continue

            if frappe.db.exists(doctype, company):
                doc = frappe.get_doc(doctype, company)
            else:
                doc = frappe.new_doc(doctype)
                doc.company = company

            for row in rows:
                if any(row[c] for c in cols):
                    doc.append("mappings", {c: row[c] for c in cols})

            doc.flags.ignore_mandatory = True
            doc.flags.ignore_permissions = True
            doc.save()

        for c in cols:
            frappe.db.sql_ddl(f"alter table `tab{doctype}` drop column `{c}`")

    frappe.clear_cache()
