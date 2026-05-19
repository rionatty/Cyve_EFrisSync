"""
Excise duty metadata helper for EFRIS T109 goodsDetails.

API spec (T109 documentation):
  exciseFlag   "1" = item attracts excise duty   "2" = no excise
  exciseRule   "1" = percentage (ad valorem)     "2" = quantity/unit
  exciseRate   decimal string: "0.1" for 10%,  "0.18" for 18%
               (NOT the percentage integer — same format as taxRate)
  exciseRateName  MANDATORY when exciseFlag="1" (rule 2832)
               rule=1: "<pct>%"  e.g. "10%"
               rule=2: "<currency><rate> per <unit>"  e.g. "UGX650 per litre"
  exciseTax    total excise tax for this line item — computed by caller
               using _trunc2(line_net * rate / (1 + rate), 2)
  exciseCurrency  currency name string: "UGX"  (only required for rule=2)
  exciseUnit   URA unit code: 101=per stick, 102=per litre, 103=per kg …
               (only required for rule=2)
  pack / stick  only required for exciseRule="2"

When exciseFlag="2":
  exciseRate   MUST be empty   (rule 1232)
  exciseTax    MUST be empty or "0"  (rule 2790)
  exciseRule   not validated but send "" to be safe

taxDetails:  a SEPARATE row with taxCategoryCode="05" must be included
             taxRateName is MANDATORY for code "05"  (rule 2831)
"""

import frappe
from efris_integration.utils.helpers import safe_float


def compute_excise_for_line(line, item_fields: dict, settings, currency: str) -> dict:
    """
    Return excise metadata fields ready to be merged into a goodsDetails item.

    NOTE: exciseTax is set to "0" here.  The caller (invoice.py / _build_goods_and_tax)
    must overwrite it with the correctly truncated value computed from line_net:
        exciseTax = _trunc2(line_net * excise_decimal / (1 + excise_decimal))

    Returns a dict with ALL excise keys so the goodsDetails payload is always
    complete.  For non-excise items (exciseFlag="2") all excise values are empty.
    """
    excise_flag = str(item_fields.get("efris_excise_flag") or "0").strip()

    if excise_flag != "1":
        # Non-excisable item — send flag="2" and empty values for all other fields
        # rule 1232: exciseRate MUST be empty when exciseFlag="2"
        # rule 2790: exciseTax MUST be empty or "0" when exciseFlag="2"
        return {
            "exciseFlag":     "2",
            "exciseRate":     "",
            "exciseRule":     "",
            "exciseTax":      "",
            "pack":           "",
            "stick":          "",
            "exciseUnit":     "",
            "exciseCurrency": "",
            "exciseRateName": "",
        }

    # ── Excisable item ────────────────────────────────────────────────────────
    excise_rate_pct     = safe_float(item_fields.get("efris_excise_rate") or 0)
    excise_rate_decimal = excise_rate_pct / 100          # 10.0 → 0.1

    # Format as decimal string, no trailing zeros: 0.1 not 0.10
    excise_rate_str = f"{excise_rate_decimal:g}"         # "0.1", "0.18", "0.6"

    # All URA excise goods in Uganda use ad valorem (percentage) rule
    # exciseRule="1" = percentage;  exciseRule="2" = quantity/unit of measurement
    excise_rule = "1"

    # exciseRateName: MANDATORY for exciseFlag="1" (rule 2832)
    # For rule=1: "(rate_pct)%"  e.g. "10%"
    if excise_rate_pct == int(excise_rate_pct):
        excise_rate_name = f"{int(excise_rate_pct)}%"
    else:
        excise_rate_name = f"{excise_rate_pct}%"

    # pack/stick/exciseUnit/exciseCurrency only required for exciseRule="2"
    # Leave empty for percentage (rule="1") to avoid invalid-field errors
    return {
        "exciseFlag":     "1",
        "exciseRate":     excise_rate_str,   # "0.1" for 10% — decimal, NOT integer
        "exciseRule":     excise_rule,        # "1" = ad valorem percentage
        "exciseTax":      "0",               # placeholder — caller overwrites this
        "pack":           "",                # not required for exciseRule="1"
        "stick":          "",                # not required for exciseRule="1"
        "exciseUnit":     "",                # not required for exciseRule="1"
        "exciseCurrency": "",                # not required for exciseRule="1"
        "exciseRateName": excise_rate_name,  # e.g. "10%"  — MANDATORY (rule 2832)
    }


def _get_usd_rate() -> float:
    """Get current USD→UGX exchange rate from ERPNext Currency Exchange."""
    from frappe.utils import today

    rate = frappe.db.get_value(
        "Currency Exchange",
        {"from_currency": "USD", "to_currency": "UGX", "date": today()},
        "exchange_rate",
    )
    if not rate:
        rate = frappe.db.get_value(
            "Currency Exchange",
            {"from_currency": "USD", "to_currency": "UGX"},
            "exchange_rate",
            order_by="date desc",
        )
    return float(rate) if rate else 3800.0


# ─── Bulk excise recalculation (called from Sales Order / Sales Invoice form) ───

@frappe.whitelist()
def recalculate_excise(doctype: str, docname: str) -> dict:
    """
    Recalculate excise duty for all lines in a Sales Order or Sales Invoice.
    Saves updated excise amounts back to the document lines.
    Called from the EFRIS toolbar button.
    """
    from efris_integration.utils.helpers import get_item_efris_fields, get_efris_settings

    doc      = frappe.get_doc(doctype, docname)
    settings = get_efris_settings(doc.company)

    total_excise = 0.0
    for line in doc.items:
        item_fields = get_item_efris_fields(line.item_code)
        excise_data = compute_excise_for_line(line, item_fields, settings, doc.currency)
        # For display purposes use a simple percentage calculation
        if excise_data.get("exciseFlag") == "1":
            rate_decimal = safe_float(excise_data.get("exciseRate") or 0)
            net_amt = safe_float(getattr(line, "net_amount", None) or line.amount)
            excise_amt = round(net_amt * rate_decimal / (1 + rate_decimal), 2)
        else:
            excise_amt = 0.0
        line.efris_excise_tax = excise_amt
        total_excise += excise_amt

    doc.efris_total_excise = total_excise
    doc.save()
    frappe.msgprint(f"Excise recalculated. Total: UGX {total_excise:,.2f}")
    return {"total_excise": total_excise}
