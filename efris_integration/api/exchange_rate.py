"""
T126 — Fetch URA Exchange Rates
T120 — Exchange Rate model

Mirrors: ExchangeRateForm.cs, T120.cs

SAP B1 flow (ExchangeRateForm.cs → FetchAndUpdateExchangeRates):
  1. Build payload:  {"queryDate": "yyyy-MM-dd 00:00:00"}
  2. AES-128-ECB encrypt → base64  (same as every other T-code)
  3. RSA SHA1 PKCS1v1.5 sign the base64 content string
     → data.signature field in the request envelope
  4. POST T126; parse list of T120 objects
  5. T120.cs fields (exact lowercase): currency, rate, nowTime
  6. Skip UGX (local base currency)
  7. Upsert into ERPNext Currency Exchange (from_currency → UGX)

Scheduler: runs daily at midnight (see hooks.py scheduler_events)
Manual:    EFRIS Settings → "Fetch URA Exchange Rates" button
"""

from datetime import date, datetime, timedelta

import frappe

from efris_integration.api.base import EFRISClient, EFRISAPIError


# ─── Public API ───────────────────────────────────────────────────────────────

@frappe.whitelist()
def sync_exchange_rates(from_date: str = None, to_date: str = None) -> dict:
    """
    Fetch EFRIS exchange rates for a date range and upsert into
    ERPNext Currency Exchange.

    Args:
        from_date: "YYYY-MM-DD" — defaults to today
        to_date:   "YYYY-MM-DD" — defaults to today

    Returns:
        {"synced": N, "date": "YYYY-MM-DD"}   (date of last fetched day)
    """
    today_str = date.today().isoformat()
    from_date = from_date or today_str
    to_date   = to_date   or today_str

    try:
        d_from = date.fromisoformat(from_date)
        d_to   = date.fromisoformat(to_date)
    except ValueError as exc:
        frappe.throw(f"Invalid date format: {exc}")

    client = EFRISClient()
    total_saved = 0
    last_date   = today_str

    current = d_from
    while current <= d_to:
        saved, fetched_date = _fetch_and_upsert(client, current)
        total_saved += saved
        last_date    = fetched_date or current.isoformat()
        current     += timedelta(days=1)

    frappe.db.commit()

    msg = f"EFRIS T126: {total_saved} exchange rate(s) updated for {last_date}."
    frappe.msgprint(msg, indicator="green")
    return {"synced": total_saved, "date": last_date}


# ─── Internal ─────────────────────────────────────────────────────────────────

def _fetch_and_upsert(client: EFRISClient, query_date: date) -> tuple[int, str]:
    """
    Send one T126 request for query_date, parse T120 list, upsert rates.
    Returns (count_saved, date_string).

    Mirrors ExchangeRateForm.cs exactly:
        payload      = {"queryDate": "yyyy-MM-dd 00:00:00"}
        encrypt=True → AES-128-ECB → base64 → content
        sign=True    → RSA SHA1 PKCS1v1.5 of content string → signature
    """
    # ExchangeRateForm.cs: queryDate = date.ToString("yyyy-MM-dd") + " 00:00:00"
    query_date_str = query_date.strftime("%Y-%m-%d") + " 00:00:00"
    payload = {"queryDate": query_date_str}

    try:
        result = client.call(
            "T126",
            payload=payload,
            encrypt=True,
            sign=True,
            doc_type="Currency Exchange",
            doc_name="",
        )
    except EFRISAPIError as exc:
        frappe.log_error(
            f"T126 failed for {query_date_str}: [{exc.code}] {exc.message}",
            "EFRIS Exchange Rate Error",
        )
        frappe.throw(
            f"T126 (exchange rate) failed: [{exc.code}] {exc.message}<br>"
            "If the session key has expired, run <b>Full Initialise (T101→T104)</b> first."
        )

    # Response is a list of T120 objects — may arrive as list or {"data": [...]}
    rate_list = _extract_rate_list(result)
    if not rate_list:
        frappe.log_error(
            f"T126 returned no rate list for {query_date_str}. Raw: {result}",
            "EFRIS Exchange Rate Warning",
        )
        return 0, query_date.isoformat()

    date_str  = query_date.isoformat()
    saved     = 0
    now_time  = ""   # will be set from first record that has nowTime

    for item in rate_list:
        # T120.cs exact field names (all lowercase)
        currency = (item.get("currency") or "").strip().upper()
        rate_val = item.get("rate")
        now_time = item.get("nowTime") or now_time

        if not currency or rate_val is None:
            continue

        # Skip UGX — it is the base currency
        if currency == "UGX":
            continue

        try:
            rate_float = float(rate_val)
        except (TypeError, ValueError):
            frappe.log_error(
                f"T126: cannot parse rate '{rate_val}' for currency '{currency}'",
                "EFRIS Exchange Rate Warning",
            )
            continue

        if rate_float <= 0:
            continue

        _upsert_currency_exchange(currency, rate_float, date_str)
        saved += 1

    # Use nowTime from the EFRIS response as the canonical date if available
    canonical_date = _parse_now_time(now_time) or date_str
    return saved, canonical_date


def _extract_rate_list(result) -> list:
    """
    T126 response data may arrive in several forms:
      - A plain Python list (most common after _parse_plain_content)
      - A dict with a "data" key containing the list
      - A dict whose first value is the list
    """
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        # {"data": [...]}  or  {"rates": [...]}
        for key in ("data", "rates", "exchangeRates", "rateList"):
            val = result.get(key)
            if isinstance(val, list):
                return val
        # Fallback: first list-valued key
        for val in result.values():
            if isinstance(val, list):
                return val
    return []


def _upsert_currency_exchange(currency: str, rate: float, date_str: str):
    """
    Insert or update a Currency Exchange record (from_currency → UGX).
    Mirrors: ExchangeRateForm.cs SetCurrencyExchangeRate()
    """
    existing = frappe.db.get_value(
        "Currency Exchange",
        {
            "from_currency": currency,
            "to_currency":   "UGX",
            "date":          date_str,
        },
        "name",
    )
    if existing:
        frappe.db.set_value("Currency Exchange", existing, "exchange_rate", rate)
    else:
        # Ensure the currency exists in ERPNext master
        _ensure_currency(currency)
        frappe.get_doc(
            {
                "doctype":       "Currency Exchange",
                "from_currency": currency,
                "to_currency":   "UGX",
                "date":          date_str,
                "exchange_rate": rate,
                "for_buying":    1,
                "for_selling":   1,
            }
        ).insert(ignore_permissions=True)


def _ensure_currency(currency: str):
    """Create the Currency master if it doesn't already exist."""
    if not frappe.db.exists("Currency", currency):
        try:
            frappe.get_doc(
                {
                    "doctype":       "Currency",
                    "currency_name": currency,
                    "enabled":       1,
                }
            ).insert(ignore_permissions=True)
        except Exception:
            pass   # Concurrent insert or validation — non-fatal


def _parse_now_time(now_time: str) -> str | None:
    """
    T120.nowTime arrives as "yyyy-MM-dd HH:mm:ss" or "yyyy-MM-dd".
    Returns "YYYY-MM-DD" string, or None if unparseable.
    """
    if not now_time:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(now_time.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None
