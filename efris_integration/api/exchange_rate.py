"""
T120 — Get Exchange Rates from EFRIS
T126 — Fetch and persist exchange rates to ERPNext Currency Exchange

Mirrors: ExchangeRateForm.cs, T120.cs

EFRIS returns a list of currency/rate pairs. We write them into ERPNext's
Currency Exchange doctype so the rest of the system uses them.
"""

from datetime import date

import frappe

from efris_integration.api.base import EFRISClient


@frappe.whitelist()
def get_exchange_rates() -> list:
    """T120 — Retrieve current exchange rates from EFRIS."""
    client = EFRISClient()
    result = client.call("T120", doc_type="Currency Exchange", doc_name="")
    rates = result if isinstance(result, list) else result.get("rates", [])
    return rates


@frappe.whitelist()
def sync_exchange_rates() -> dict:
    """
    T126 — Fetch EFRIS exchange rates and push them into ERPNext Currency Exchange.
    Called daily by scheduler and can be triggered manually.
    """
    client = EFRISClient()

    try:
        result = client.call("T126", doc_type="Currency Exchange", doc_name="")
    except Exception:
        # Fall back to T120 if T126 not available
        result = client.call("T120", doc_type="Currency Exchange", doc_name="")

    rates = result if isinstance(result, list) else result.get("rates", [])
    today = date.today().isoformat()
    saved = 0

    for rate_item in rates:
        currency = rate_item.get("currency", "").upper()
        rate_val = rate_item.get("rate") or rate_item.get("exchangeRate")
        if not currency or not rate_val:
            continue

        try:
            rate_float = float(rate_val)
        except (TypeError, ValueError):
            continue

        # Upsert Currency Exchange record for today
        existing = frappe.db.get_value(
            "Currency Exchange",
            {"from_currency": currency, "to_currency": "UGX", "date": today},
            "name",
        )
        if existing:
            frappe.db.set_value("Currency Exchange", existing, "exchange_rate", rate_float)
        else:
            frappe.get_doc(
                {
                    "doctype": "Currency Exchange",
                    "from_currency": currency,
                    "to_currency": "UGX",
                    "date": today,
                    "exchange_rate": rate_float,
                    "for_buying": 1,
                    "for_selling": 1,
                }
            ).insert(ignore_permissions=True)
        saved += 1

    frappe.db.commit()
    msg = f"EFRIS exchange rates synced: {saved} currencies updated."
    frappe.msgprint(msg)
    return {"synced": saved, "date": today}
