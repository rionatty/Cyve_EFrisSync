"""
T126 — Fetch URA Exchange Rates
T120 — Exchange Rate model

Mirrors: ExchangeRateForm.cs, T120.cs

SAP B1 flow (ExchangeRateForm.cs → FetchAndUpdateExchangeRates):
  1. Build payload:  {"queryDate": "yyyy-MM-dd 00:00:00"}
  2. AES-128-ECB encrypt payload + RSA SHA1 signature
  3. POST T126; parse list of T120 objects
  4. T120 fields (exact lowercase): currency, rate, nowTime
  5. Skip UGX (local base currency)
  6. Upsert into ERPNext Currency Exchange (from_currency → UGX)
"""

from datetime import date, datetime, timedelta

import frappe

from efris_integration.api.base import EFRISClient, EFRISAPIError


@frappe.whitelist()
def sync_exchange_rates(from_date: str = None, to_date: str = None) -> dict:
    """
    Fetch EFRIS exchange rates and upsert into ERPNext Currency Exchange.
    Called by the "Fetch URA Exchange Rates" button and daily scheduler.
    """
    today_str = date.today().isoformat()
    from_date = from_date or today_str
    to_date   = to_date   or today_str

    try:
        d_from = date.fromisoformat(from_date)
        d_to   = date.fromisoformat(to_date)
    except ValueError as exc:
        frappe.throw(f"Invalid date: {exc}")

    client      = EFRISClient()
    total_saved = 0
    last_date   = today_str

    current = d_from
    while current <= d_to:
        saved, fetched_date = _fetch_one_day(client, current)
        total_saved += saved
        last_date    = fetched_date or current.isoformat()
        current     += timedelta(days=1)

    frappe.db.commit()
    frappe.msgprint(
        f"✅ {total_saved} exchange rate(s) updated for {last_date}.",
        indicator="green",
    )
    return {"synced": total_saved, "date": last_date}


def _fetch_one_day(client: EFRISClient, query_date: date) -> tuple[int, str]:
    """Call T126 for one date, parse T120 list, upsert rates.
    Auto-refreshes the session once if the AES key is stale (code 15/16/99)."""
    query_date_str = query_date.strftime("%Y-%m-%d") + " 00:00:00"

    try:
        result = _call_t126(client, query_date_str)
    except EFRISAPIError as exc:
        # Session-expired codes — refresh T103+T104 and retry once silently
        if exc.code in ("15", "16", "99", "SESSION_EXPIRED"):
            frappe.logger("efris").info(
                f"T126 [{exc.code}] — refreshing session (T103+T104) and retrying."
            )
            _refresh_session(client)
            try:
                result = _call_t126(client, query_date_str)
            except EFRISAPIError as exc2:
                frappe.log_error(
                    f"T126 [{exc2.code}] {exc2.message} after session refresh",
                    "EFRIS Exchange Rate Error",
                )
                frappe.throw(
                    f"❌ T126 failed: [{exc2.code}] {exc2.message}",
                    title="Exchange Rate Error",
                )
        else:
            frappe.log_error(
                f"T126 [{exc.code}] {exc.message} for {query_date_str}",
                "EFRIS Exchange Rate Error",
            )
            frappe.throw(f"❌ T126 failed: [{exc.code}] {exc.message}", title="Exchange Rate Error")

    rate_list = _extract_rate_list(result)
    if not rate_list:
        frappe.logger("efris").warning(f"T126: no rates for {query_date_str}. Raw: {result}")
        return 0, query_date.isoformat()

    date_str = query_date.isoformat()
    saved    = 0
    now_time = ""

    for item in rate_list:
        currency = (item.get("currency") or "").strip().upper()
        rate_val = item.get("rate")
        now_time = item.get("nowTime") or now_time

        if not currency or rate_val is None or currency == "UGX":
            continue
        try:
            rate_float = float(rate_val)
        except (TypeError, ValueError):
            continue
        if rate_float <= 0:
            continue

        _upsert_rate(currency, rate_float, date_str)
        saved += 1

    return saved, _parse_date(now_time) or date_str


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _call_t126(client: EFRISClient, query_date_str: str) -> dict:
    """POST T126 with AES-encrypted + RSA-signed queryDate payload."""
    return client.call(
        "T126",
        payload={"queryDate": query_date_str},
        encrypt=True,
        sign=True,
        doc_type="Currency Exchange",
        doc_name="",
    )


def _refresh_session(client: EFRISClient):
    """
    Renew the EFRIS session: T102 (fresh private key) → T103 (sign-in) → T104 (new AES key).
    Running T102 ensures the private key on disk is current before T104 tries to use it.
    Resets the cached AES key so the next T126 call reads the fresh key from file.
    """
    from efris_integration.api.auth import client_init, sign_in, get_symmetric_key
    client_init()       # T102 — fresh private key + server public key
    sign_in()           # T103 — sign in
    get_symmetric_key() # T104 — new session AES key
    client._aes_key = None   # force reload from SessionAESKey.txt on next call


def _extract_rate_list(result) -> list:
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in ("data", "rates", "exchangeRates", "rateList"):
            val = result.get(key)
            if isinstance(val, list):
                return val
        for val in result.values():
            if isinstance(val, list):
                return val
    return []


def _upsert_rate(currency: str, rate: float, date_str: str):
    existing = frappe.db.get_value(
        "Currency Exchange",
        {"from_currency": currency, "to_currency": "UGX", "date": date_str},
        "name",
    )
    if existing:
        frappe.db.set_value("Currency Exchange", existing, "exchange_rate", rate)
    else:
        if not frappe.db.exists("Currency", currency):
            try:
                frappe.get_doc({
                    "doctype": "Currency", "currency_name": currency, "enabled": 1,
                }).insert(ignore_permissions=True)
            except Exception:
                pass
        frappe.get_doc({
            "doctype":       "Currency Exchange",
            "from_currency": currency,
            "to_currency":   "UGX",
            "date":          date_str,
            "exchange_rate": rate,
            "for_buying":    1,
            "for_selling":   1,
        }).insert(ignore_permissions=True)


def _parse_date(val: str) -> str | None:
    if not val:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(val.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None
