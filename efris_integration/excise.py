"""
Excise duty — mirrors the SAP B1 add-on (ARInvoice.cs / Delivery.cs / SalesOrder.cs,
ARCreditNote.cs, ExciseDuty.cs).

1. Line calculation (apply_excise) — Sales Order, Delivery Note, Sales Invoice.
   Runs in before_validate (so ERPNext's own totals and taxes see the result)
   and from the form for a live preview (preview_excise). Only when EFRIS
   Settings → "Calculate Excise on Sales Documents" is on, and then per
   document type in one of two modes:

   Unit price  (Price On Sales Order / Delivery / AR Invoice)
       The line rate is the price before duty. Duty per unit is added to it:
       rate = base price + duty, and the base price is kept on the line.
   Gross price (Gross On ..., used when the unit-price switch is off)
       The user enters the VAT-inclusive Gross Unit Price; it is reduced by
       the line discount, VAT is backed out (18% lines), and the duty is
       backed out of what remains. rate = gross ex-VAT price (incl. duty).

   Duty per unit (ex-VAT, in the document currency):
       Percentage   price × rate%                      (gross mode: p − p/(1+r))
       Per tonne    USD/tonne × weight(kg)/1000 × USD rate on the posting date
                    — the larger of the two wins (SAP DutyRateType 1 / 2)
       Beverage     UGX per litre × litres per unit    (EFRIS Settings: Beverage Industry)
       Alcohol      max(60% of price, UGX 1,500)        (EFRIS Settings: Alcohol Industry)
                    SAP uses 150 in one code path — a typo; 1,500 everywhere else
                    and in the T130 goods registration.

   Skipped for foreign (B2F) and export customers and for items whose
   "Have Excise Tax" is not Yes; excise is removed again if a line stops
   qualifying. Saving is blocked when a liable line ends up with no excise.

2. Journal entry (post_excise_journal) — on Sales Invoice submit when EFRIS
   Settings → "Post Excise Duty Journal Entry" is on: each line's income
   account is debited with its excise and the Excise Duty Account credited;
   reversed for credit notes. Cancelled with the invoice.

3. T109 metadata (compute_excise_for_line) — sends each line's stored excise.
"""

import json

import frappe
from frappe.utils import flt, getdate

from efris_integration.utils.helpers import (
    get_customer_efris_fields,
    get_efris_settings,
    get_item_efris_fields,
    safe_float,
)

ALCOHOL_RATE = 0.6
ALCOHOL_MIN_UGX = 1500.0

# (unit-price switch, gross-price switch) per document type
MODE_FIELDS = {
    "Sales Order":   ("price_on_sales_order", "gross_on_sales_order"),
    "Delivery Note": ("price_on_delivery", "gross_on_delivery"),
    "Sales Invoice": ("price_on_ar_invoice", "gross_on_ar_invoice"),
}

LINE_FIELDS = ("efris_base_price", "efris_excise_basis", "efris_excise_per_unit",
               "efris_unit_price_incl_excise", "efris_excise_tax")


# ─── 1. Line calculation ─────────────────────────────────────────────────────

def before_validate(doc, method=None):
    apply_excise(doc)


def apply_excise(doc, settings=None) -> bool:
    """Calculate excise on every line of `doc` in place. Returns False when off."""
    if settings is None:
        if not frappe.db.exists("EFRIS Settings", {"company": doc.company}):
            return False
        settings = get_efris_settings(doc.company)
    mode = excise_mode(doc, settings)
    if not mode:
        return False

    buyer = get_customer_efris_fields(doc.customer) if doc.get("customer") else {}
    foreign = (str(buyer.get("efris_business_type") or "").upper() == "B2F"
               or str(buyer.get("efris_export") or "").upper().startswith("Y"))

    from efris_integration.api.invoice import _is_tax_included_in_rate, _resolve_line_tax
    tax_included = _is_tax_included_in_rate(doc)

    total = 0.0
    problems = []
    for line in doc.items:
        item = get_item_efris_fields(line.item_code) if line.item_code else {}
        if foreign or not is_excise_item(item):
            _clear(line)
            continue
        vat_pct = _resolve_line_tax(doc, line, doc.company)[0]
        vat = 0.18 if vat_pct == 18.0 else 0.0
        ok = _apply_line(doc, line, item, mode, settings, vat, tax_included)
        if not ok and flt(line.qty):
            problems.append(_problem(line, mode))
        total += flt(line.get("efris_excise_tax"))

    if doc.meta.has_field("efris_total_excise"):
        doc.efris_total_excise = flt(total, 2)
    if problems and not frappe.flags.get("efris_excise_preview"):
        frappe.throw("<br>".join(problems), title="Excise Duty Not Calculated")
    return True


def excise_mode(doc, settings) -> str:
    """'unit', 'gross' or '' for this document type (unit price wins, as in SAP)."""
    if not settings.get("calculate_excise"):
        return ""
    unit_field, gross_field = MODE_FIELDS.get(doc.doctype, (None, None))
    if not unit_field:
        return ""
    if settings.get(unit_field):
        return "unit"
    if settings.get(gross_field):
        return "gross"
    return ""


def is_excise_item(item: dict) -> bool:
    return (str(item.get("efris_have_excise_tax") or "").strip().lower() == "yes"
            or str(item.get("efris_excise_flag") or "") == "1")


def _apply_line(doc, line, item, mode, settings, vat, tax_included) -> bool:
    qty = flt(line.qty)
    rate = flt(line.rate)
    stored_base = flt(line.get("efris_base_price"))
    stored_incl = flt(line.get("efris_unit_price_incl_excise"))

    if mode == "unit" or settings.get("alcohol_industry"):
        # Base price: the stored one, unless the user changed the rate since the
        # last calculation (then the new rate is the new base price).
        base = stored_base if stored_base and abs(rate - stored_incl) < 0.005 else rate
        if base <= 0 or not qty:
            _set(line, base=base, basis="", per_unit=0, incl=0, total=0)
            return False
        base_ex = base / (1 + vat) if tax_included else base
        duty, basis = _unit_duty(doc, item, base_ex, settings)
        incl_ex = base_ex + duty
        new_rate = incl_ex * (1 + vat) if tax_included else incl_ex
    else:
        gross = flt(line.get("efris_gross_price"))
        if gross <= 0 or not qty:
            _set(line, base=0, basis="", per_unit=0, incl=0, total=0)
            return False
        if flt(line.get("discount_percentage")):
            gross -= gross * flt(line.discount_percentage) / 100
        price_ex = gross / (1 + vat)
        duty, basis = _gross_duty(doc, item, price_ex, settings)
        base_ex = price_ex - duty
        new_rate = gross if tax_included else price_ex
        base = base_ex * (1 + vat) if tax_included else base_ex

    new_rate = flt(new_rate, _precision(line, "rate"))
    duty = flt(duty, 6)
    if abs(rate - new_rate) >= 1e-9:
        line.rate = new_rate
    # The discount is already inside the price (SAP resets it to 0), and the
    # price list rate must equal the rate, or the URA upload would read the
    # difference as a discount and send a discount line.
    if flt(line.get("discount_percentage")) or flt(line.get("discount_amount")):
        line.discount_percentage = 0
        line.discount_amount = 0
    if flt(line.get("price_list_rate")) and abs(flt(line.price_list_rate) - new_rate) >= 1e-9:
        line.price_list_rate = new_rate
    _set(line, base=base, basis=basis, per_unit=duty, incl=new_rate, total=flt(duty * qty, 2))
    return duty > 0


def _unit_duty(doc, item, price_ex, settings) -> tuple:
    if settings.get("alcohol_industry"):
        return max(price_ex * ALCOHOL_RATE, _convert(doc, "UGX", ALCOHOL_MIN_UGX)), "Alcohol"
    if settings.get("beverage_industry"):
        return _per_litre(doc, item), "Per Litre"
    pct = price_ex * safe_float(item.get("efris_excise_rate")) / 100
    return _larger(pct, _per_tonne(doc, item))


def _gross_duty(doc, item, price_ex, settings) -> tuple:
    if settings.get("beverage_industry"):
        # SAP has no beverage branch in gross mode (it misapplies the per-litre
        # rate as a %); back out the per-litre duty instead.
        return _per_litre(doc, item), "Per Litre"
    r = safe_float(item.get("efris_excise_rate")) / 100
    pct = price_ex - price_ex / (1 + r) if r else 0.0
    return _larger(pct, _per_tonne(doc, item))


def _larger(pct: float, per_tonne: float) -> tuple:
    # SAP: per tonne only when strictly larger (a tie, incl. 0/0, is "Percentage")
    return (per_tonne, "Per Tonne") if per_tonne > pct else (pct, "Percentage")


def _per_tonne(doc, item) -> float:
    usd_per_tonne = safe_float(item.get("efris_excise_rate_usd"))
    kg = safe_float(item.get("efris_weight_unit"))
    if not (usd_per_tonne and kg):
        return 0.0
    return _convert(doc, "USD", usd_per_tonne * kg / 1000)


def _per_litre(doc, item) -> float:
    return _convert(doc, "UGX", safe_float(item.get("efris_excise_rate")) * safe_float(item.get("volume_per_unit")))


def _convert(doc, from_currency: str, amount: float) -> float:
    to_currency = doc.currency or from_currency
    if not amount or from_currency == to_currency:
        return amount
    from erpnext.setup.utils import get_exchange_rate
    date = doc.get("posting_date") or doc.get("transaction_date")
    rate = flt(get_exchange_rate(from_currency, to_currency, date))
    if not rate:
        frappe.throw(
            f"No {from_currency} to {to_currency} exchange rate for {date}, needed for excise duty. "
            "Fetch URA exchange rates in EFRIS Settings or add a Currency Exchange.",
            title="Excise Duty",
        )
    return amount * rate


def _set(line, base, basis, per_unit, incl, total):
    values = {"efris_base_price": base, "efris_excise_basis": basis, "efris_excise_per_unit": per_unit,
              "efris_unit_price_incl_excise": incl, "efris_excise_tax": total}
    for k, v in values.items():
        if line.meta.has_field(k):
            line.set(k, v)


def _clear(line):
    """Line no longer liable: put the base price back if excise was added to it."""
    base = flt(line.get("efris_base_price"))
    incl = flt(line.get("efris_unit_price_incl_excise"))
    if base and incl and abs(flt(line.rate) - incl) < 0.005:
        line.rate = base
    _set(line, base=0, basis="", per_unit=0, incl=0, total=0)


def _problem(line, mode) -> str:
    what = (f"enter the <b>Gross Unit Price</b>" if mode == "gross" and not flt(line.get("efris_gross_price"))
            else "the price must be above zero")
    return f"Row {line.idx} ({line.item_code}): excise duty could not be calculated — {what}."


def _precision(line, field) -> int:
    try:
        return line.precision(field) or 2
    except Exception:
        return 2


@frappe.whitelist()
def preview_excise(doc) -> dict:
    """Live preview for the form: calculate on the unsaved document, return row values."""
    doc = frappe.get_doc(json.loads(doc) if isinstance(doc, str) else doc)
    frappe.flags.efris_excise_preview = True
    try:
        enabled = apply_excise(doc)
    finally:
        frappe.flags.efris_excise_preview = False
    if not enabled:
        return {"enabled": False, "rows": []}
    rows = [{"name": line.name, "values": {f: line.get(f) for f in ("rate",) + LINE_FIELDS}}
            for line in doc.items]
    return {"enabled": True, "rows": rows}


@frappe.whitelist()
def recalculate_excise(doctype: str, docname: str) -> dict:
    """Recalculate a draft by saving it (the calculation runs on every save)."""
    doc = frappe.get_doc(doctype, docname)
    if doc.docstatus != 0:
        frappe.throw("Excise is calculated while the document is a draft.", title="Excise Duty")
    doc.save()
    return {"total_excise": flt(doc.get("efris_total_excise"))}


# ─── 2. Excise journal entry ─────────────────────────────────────────────────

@frappe.whitelist()
def post_excise_journal(invoice_name: str, show_message: bool = True) -> str:
    doc = frappe.get_doc("Sales Invoice", invoice_name)
    if doc.docstatus != 1:
        frappe.throw("Submit the invoice first.", title="Excise Journal")
    if doc.get("efris_excise_journal"):
        frappe.throw(f"Excise journal entry {doc.efris_excise_journal} is already posted.", title="Excise Journal")
    settings = get_efris_settings(doc.company)
    if not settings.get("excise_journal_entry"):
        frappe.throw("Turn on <b>Post Excise Duty Journal Entry</b> in EFRIS Settings.", title="Excise Journal")
    liability = settings.get("excise_liability_account")
    if not liability:
        frappe.throw("Set the <b>Excise Duty Account</b> in EFRIS Settings.", title="Excise Journal")
    if settings.get("excise_journal_from") and getdate(doc.posting_date) < getdate(settings.excise_journal_from):
        frappe.throw("This invoice is dated before the excise journal start date in EFRIS Settings.",
                     title="Excise Journal")

    rate = flt(doc.conversion_rate) or 1
    by_account: dict = {}
    for line in doc.items:
        amount = flt(line.get("efris_excise_tax")) * rate
        if amount:
            key = (line.income_account, line.cost_center)
            by_account[key] = by_account.get(key, 0.0) + amount
    rows, total = [], 0.0
    for (account, cost_center), amount in by_account.items():
        amount = flt(amount, 2)
        total += amount
        rows.append(_je_row(account, amount, cost_center))   # invoice: debit income
    total = flt(total, 2)
    if not total:
        frappe.throw("There is no excise duty on this invoice.", title="Excise Journal")
    rows.append(_je_row(liability, -total, None))              # invoice: credit liability

    kind = "credit note" if doc.is_return else "invoice"
    je = frappe.get_doc({
        "doctype": "Journal Entry",
        "voucher_type": "Journal Entry",
        "company": doc.company,
        "posting_date": doc.posting_date,
        "user_remark": f"Excise duty for {kind} {doc.name}",
        "accounts": rows,
    })
    je.insert(ignore_permissions=True)
    je.submit()
    doc.db_set("efris_excise_journal", je.name, update_modified=False)
    if show_message:
        frappe.msgprint(f"Excise journal entry {je.name} posted.", title="Excise Journal", indicator="green")
    return je.name


def _je_row(account, amount, cost_center) -> dict:
    """Positive amount = debit, negative = credit (credit notes come out reversed)."""
    row = {"account": account,
           "debit_in_account_currency": amount if amount > 0 else 0,
           "credit_in_account_currency": -amount if amount < 0 else 0}
    if cost_center:
        row["cost_center"] = cost_center
    return row


def auto_post_excise_journal(doc) -> None:
    """on_submit: post the journal entry when switched on; never blocks the submit."""
    if not flt(doc.get("efris_total_excise")) or doc.get("efris_excise_journal"):
        return
    if not frappe.db.get_value("EFRIS Settings", {"company": doc.company}, "excise_journal_entry"):
        return
    frappe.db.savepoint("efris_excise_je")
    try:
        post_excise_journal(doc.name, show_message=False)
    except Exception as exc:
        frappe.db.rollback(save_point="efris_excise_je")
        frappe.log_error(frappe.get_traceback(), f"EFRIS excise journal {doc.name}")
        frappe.clear_messages()
        frappe.msgprint(f"Invoice saved, but the excise journal entry was not posted: {exc}<br>"
                        "Use <b>EFRIS → Post Excise Journal</b> after fixing it.",
                        title="Excise Journal", indicator="orange")


def cancel_excise_journal(doc) -> None:
    name = doc.get("efris_excise_journal")
    if name and frappe.db.get_value("Journal Entry", name, "docstatus") == 1:
        frappe.get_doc("Journal Entry", name).cancel()


# ─── 3. T109 goodsDetails excise fields ──────────────────────────────────────

def compute_excise_for_line(line, item_fields: dict, settings, currency: str) -> dict:
    """
    Excise keys for a T109 goods line. When the line carries a calculated
    excise (efris_excise_tax), that amount is sent as-is ("_line_tax"); the
    caller pops it. Otherwise a percentage is returned and the caller backs
    the duty out of the line (older documents).
    Rules: exciseFlag "2" ⇒ rate empty (1232) and tax empty/0 (2790);
    exciseRateName is mandatory for flag "1" (2832).
    """
    if not is_excise_item(item_fields):
        return {"exciseFlag": "2", "exciseRate": "", "exciseRule": "", "exciseTax": "", "pack": "",
                "stick": "", "exciseUnit": "", "exciseCurrency": "", "exciseRateName": ""}

    rate_pct = safe_float(item_fields.get("efris_excise_rate"))
    data = {"exciseFlag": "1", "exciseRule": "1", "exciseRate": f"{rate_pct / 100:g}", "exciseTax": "0",
            "pack": "", "stick": "", "exciseUnit": "", "exciseCurrency": "",
            "exciseRateName": f"{rate_pct:g}%"}

    line_tax = abs(flt(line.get("efris_excise_tax")))
    if not line_tax:
        return data

    basis = line.get("efris_excise_basis") or "Percentage"
    pack = str(item_fields.get("efris_excise_weight") or "").strip()
    stick = str(item_fields.get("efris_piece_scaled_value") or "").strip()
    if not safe_float(stick):
        stick = "0" if item_fields.get("efris_stick") else ""
    if basis == "Alcohol":
        data.update({"exciseRule": "1", "exciseRate": f"{ALCOHOL_RATE:g}", "exciseRateName": "60%",
                     "exciseUnit": "102", "exciseCurrency": currency, "pack": "1", "stick": "1"})
    elif basis == "Per Tonne":
        usd = safe_float(item_fields.get("efris_excise_rate_usd"))
        data.update({"exciseRule": "2", "exciseRate": f"{usd:g}", "exciseRateName": f"USD{usd:g} per tonne",
                     "exciseUnit": "TNE", "exciseCurrency": currency, "pack": pack, "stick": stick})
    elif basis == "Per Litre":
        data.update({"exciseRule": "2", "exciseRate": f"{rate_pct:g}", "exciseRateName": f"UGX{rate_pct:g} per litre",
                     "exciseUnit": "LTR", "exciseCurrency": currency, "pack": pack, "stick": stick})
    data["_line_tax"] = line_tax
    return data
