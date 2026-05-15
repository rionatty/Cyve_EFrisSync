"""
Excise duty calculation engine.

Mirrors: ExciseDuty.cs, UraARInvoice.cs excise block, UraSalesOrder.cs excise block

Industry modes (from EFRIS Settings):
  - Alcohol Industry:  Excise = price × rate%, with minimum UGX value check.
                       Excise per unit in USD/ton × exchange rate × weight.
  - Beverage Industry: Excise = ExciseRatePerUnit × Volume(litres) × ExchangeRate
  - Standard/Other:    Excise = ExciseRatePerTon × (Weight_kg/1000) × ExchangeRate

Item fields used:
  efris_excise_flag      — "1" = excisable, "0" = not
  efris_excise_rate      — percentage rate (e.g. 60 for 60%)
  efris_excise_rate_usd  — USD per unit for alcohol / per ton for others
  efris_weight_unit      — weight per single unit in kg
  weight_per_unit        — fallback weight field
  volume_per_unit        — volume in litres (beverage)

Returns a dict of EFRIS goodsDetails excise fields (merged into line payload).
Returns empty dict if item is not excisable.
"""

import frappe
from efris_integration.utils.helpers import safe_float


def compute_excise_for_line(line, item_fields: dict, settings, currency: str) -> dict:
    """
    Compute excise fields for a single invoice/order line.

    Returns a dict ready to be merged into the EFRIS goodsDetails line item.
    Returns {} (no excise fields) if the item is not excisable.
    """
    excise_flag = str(item_fields.get("efris_excise_flag") or "0")
    if excise_flag != "1":
        return {
            "exciseFlag": "2",
            "exciseRate": "",
            "exciseRule": "",
            "exciseTax": "",
            "pack": "",
            "stick": "",
            "exciseUnit": "",
            "exciseCurrency": "",
        }

    alcohol_mode = settings.get("alcohol_industry") == "1"
    beverage_mode = settings.get("beverage_industry") == "1"

    excise_rate_pct = safe_float(item_fields.get("efris_excise_rate"))      # e.g. 60 → 60%
    excise_rate_usd = safe_float(item_fields.get("efris_excise_rate_usd"))  # USD/ton or USD/unit
    weight_per_unit = safe_float(
        item_fields.get("efris_weight_unit") or item_fields.get("weight_per_unit")
    )
    volume_per_unit = safe_float(item_fields.get("volume_per_unit"))
    qty = safe_float(line.qty)
    unit_price = safe_float(line.rate)

    # Exchange rate: UGX per 1 USD
    exchange_rate = _get_usd_rate()

    excise_per_unit = 0.0
    total_excise = 0.0
    excise_rule = ""

    if alcohol_mode:
        # Alcohol: 60% of value, compare against minimum threshold
        value_based = unit_price * excise_rate_pct / 100
        usd_based = excise_rate_usd * exchange_rate  # USD/unit × rate
        excise_per_unit = max(value_based, usd_based)
        total_excise = excise_per_unit * qty
        excise_rule = "102"  # specific + ad valorem

    elif beverage_mode:
        # Beverage: rate × volume (litres) per unit × exchange rate
        if volume_per_unit:
            excise_per_unit = excise_rate_usd * volume_per_unit * exchange_rate
        else:
            excise_per_unit = excise_rate_pct * unit_price / 100
        total_excise = excise_per_unit * qty
        excise_rule = "103"  # specific

    else:
        # Standard / tobacco / other: USD per tonne × weight in tonnes × exchange rate
        weight_tonnes = weight_per_unit / 1000.0  # kg → tonnes
        if weight_tonnes:
            excise_per_unit = excise_rate_usd * weight_tonnes * exchange_rate
        else:
            excise_per_unit = excise_rate_pct * unit_price / 100
        total_excise = excise_per_unit * qty
        excise_rule = "101"  # specific

    return {
        "exciseFlag": "1",
        "exciseRate": str(round(excise_rate_pct, 4)),
        "exciseRule": excise_rule,
        "exciseTax": str(round(total_excise, 2)),
        "pack": str(qty),
        "stick": "1",
        "exciseUnit": "101",   # UGX
        "exciseCurrency": "101",
    }


def _get_usd_rate() -> float:
    """Get current USD→UGX exchange rate from ERPNext Currency Exchange."""
    import frappe
    from frappe.utils import today

    rate = frappe.db.get_value(
        "Currency Exchange",
        {"from_currency": "USD", "to_currency": "UGX", "date": today()},
        "exchange_rate",
    )
    if not rate:
        # Try most recent available
        rate = frappe.db.get_value(
            "Currency Exchange",
            {"from_currency": "USD", "to_currency": "UGX"},
            "exchange_rate",
            order_by="date desc",
        )
    return float(rate) if rate else 3800.0  # fallback to approximate rate


# ─── Bulk excise recalculation (called from Sales Order / Sales Invoice form) ───

@frappe.whitelist()
def recalculate_excise(doctype: str, docname: str) -> dict:
    """
    Recalculate excise duty for all lines in a Sales Order or Sales Invoice.
    Saves updated excise amounts back to the document lines.
    Called from the EFRIS toolbar button.
    """
    import frappe
    from efris_integration.utils.helpers import get_item_efris_fields

    doc = frappe.get_doc(doctype, docname)
    settings = frappe.get_single("EFRIS Settings")

    total_excise = 0.0
    for line in doc.items:
        item_fields = get_item_efris_fields(line.item_code)
        excise_data = compute_excise_for_line(line, item_fields, settings, doc.currency)
        excise_amt = float(excise_data.get("exciseTax") or 0)
        line.efris_excise_tax = excise_amt
        total_excise += excise_amt

    doc.efris_total_excise = total_excise
    doc.save()
    frappe.msgprint(f"Excise recalculated. Total: UGX {total_excise:,.2f}")
    return {"total_excise": total_excise}
