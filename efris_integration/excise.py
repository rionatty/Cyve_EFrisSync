"""
Excise duty for the URA upload (T109).

In ERPNext excise duty is an extra row in the invoice's Sales Taxes and
Charges (from the Sales Taxes and Charges Template), posting to the excise
account; VAT is charged on price + excise ("On Previous Row Total"). The app
does no excise maths of its own: it finds that row by the account set in
EFRIS Settings → "Excise Duty Account" and splits it per line
(excise_by_line), then describes it to URA (compute_excise_for_line).

T109 rules: exciseFlag "1" needs exciseRateName (2832); exciseFlag "2" needs
an empty exciseRate (1232) and an empty/0 exciseTax (2790); the excise goes
in its own taxDetails row (category 05, taxRateName mandatory, 2831).
"""

import json

import frappe
from frappe.utils import flt

from efris_integration.utils.helpers import safe_float


def excise_rows(doc, settings) -> list:
    """The invoice's tax rows that post to the Excise Duty Account."""
    account = (settings.get("excise_liability_account") or "").strip() if settings else ""
    if not account:
        return []
    return [t for t in (doc.get("taxes") or []) if t.account_head == account and flt(t.tax_amount)]


def excise_by_line(doc, settings) -> dict:
    """
    {line.name: (excise amount, rate %)} in the document currency, split from
    the excise tax row(s): ERPNext's item-wise tax breakdown when present
    (shared by lines of the same item in proportion to their net amount),
    otherwise rate × net amount ("On Net Total") or the row amount spread over
    the lines by net amount.
    """
    result: dict = {}
    for row in excise_rows(doc, settings):
        rate = flt(row.rate)
        shares = _item_wise(row)
        lines = [l for l in doc.items if l.item_code]
        for line in lines:
            if shares is not None:
                same = [l for l in lines if l.item_code == line.item_code]
                net_same = sum(flt(l.net_amount) for l in same) or len(same)
                weight = (flt(line.net_amount) or 1) / net_same if net_same else 0
                amount = shares.get(line.item_code, 0.0) * weight
            elif row.charge_type == "On Net Total":
                amount = flt(line.net_amount) * rate / 100
            else:
                total_net = sum(flt(l.net_amount) for l in lines)
                amount = flt(row.tax_amount) * flt(line.net_amount) / total_net if total_net else 0
            if amount:
                prev = result.get(line_key(line), (0.0, rate))
                result[line_key(line)] = (prev[0] + amount, rate or prev[1])
    return {k: (flt(v, 2), r) for k, (v, r) in result.items() if flt(v, 2)}


def line_key(line):
    """Stable key for a line: its name, or its row number on unsaved documents."""
    return line.name or f"row-{line.idx}"


def _item_wise(row):
    """{item_code: amount} from item_wise_tax_detail, or None if not available."""
    raw = row.get("item_wise_tax_detail")
    if not raw:
        return None
    try:
        detail = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return None
    shares = {}
    for item_code, value in (detail or {}).items():
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            shares[item_code] = safe_float(value[1])
        elif isinstance(value, dict):
            shares[item_code] = safe_float(value.get("tax_amount"))
    return shares or None


def is_excise_item(item: dict) -> bool:
    return (str(item.get("efris_have_excise_tax") or "").strip().lower() == "yes"
            or str(item.get("efris_excise_flag") or "") == "1")


def compute_excise_for_line(line, item_fields: dict, settings, currency: str,
                            excise: "tuple | None" = None) -> dict:
    """
    Excise keys for a T109 goods line. `excise` = (amount, rate %) from the
    invoice's excise tax row for this line; the amount is returned as
    "_line_tax" (the caller pops it). No excise row amount → no excise sent
    (the invoice charged none), whatever the item says.
    """
    if not excise or not excise[0]:
        return {"exciseFlag": "2", "exciseRate": "", "exciseRule": "", "exciseTax": "", "pack": "",
                "stick": "", "exciseUnit": "", "exciseCurrency": "", "exciseRateName": ""}
    amount, rate_pct = excise
    rate_pct = rate_pct or safe_float(item_fields.get("efris_excise_rate"))
    return {"exciseFlag": "1", "exciseRule": "1", "exciseRate": f"{rate_pct / 100:g}", "exciseTax": "0",
            "pack": "", "stick": "", "exciseUnit": "", "exciseCurrency": "",
            "exciseRateName": f"{rate_pct:g}%", "_line_tax": abs(amount)}
