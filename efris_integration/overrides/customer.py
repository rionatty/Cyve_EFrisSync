"""
Customer hooks.

Save: when the Tax ID changes, verify it with URA (T119) and fill Business
Type and EFRIS Invoice Name, as SAP B1 does when a BP's TIN changes.
A failure only warns; the customer is always saved.
"""

import frappe


def on_update(doc, method):
    tin = (doc.get("tax_id") or "").strip()
    if not tin or not doc.has_value_changed("tax_id"):
        return
    if frappe.flags.in_import or frappe.flags.in_migrate or frappe.flags.in_install:
        return
    company = efris_company()
    if not company:
        return

    try:
        from efris_integration.api.customer import verify_customer
        res = verify_customer(doc.name, company)
    except Exception as exc:
        frappe.log_error(frappe.get_traceback(), f"EFRIS TIN check {doc.name}")
        frappe.msgprint(f"Saved, but the TIN could not be checked with URA: {exc}",
                        title="EFRIS", indicator="orange")
        return
    # keep the form in step with what verify_customer saved
    doc.efris_business_type = doc.efris_buyer_type = res.get("business_type")
    if res.get("legal_name"):
        doc.efris_invoice_name = res["legal_name"]
    frappe.msgprint(f"TIN {tin} verified with URA: {res.get('legal_name') or '-'} ({res.get('business_type')}).",
                    title="EFRIS", indicator="green", alert=True)


def efris_company():
    """The user's default company if it has EFRIS Settings, else the only configured one."""
    default = frappe.defaults.get_user_default("Company")
    if default and frappe.db.exists("EFRIS Settings", default):
        return default
    configured = frappe.get_all("EFRIS Settings", pluck="name", limit=2)
    return configured[0] if len(configured) == 1 else None
