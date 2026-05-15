"""
T126 — Fetch URA Exchange Rates
T120 — Exchange Rate model

Mirrors: ExchangeRateForm.cs, T120.cs

SAP B1 flow (ExchangeRateForm.cs → FetchAndUpdateExchangeRates):
  1. Build payload:  {"queryDate": "yyyy-MM-dd 00:00:00"}
  2. AES-128-ECB encrypt payload (encryptCode=2)
  3. POST T126; parse list of T120 objects
  4. T120.cs fields (exact lowercase): currency, rate, nowTime
  5. Skip UGX (local base currency)
  6. Upsert into ERPNext Currency Exchange (from_currency → UGX)

If the session AES key is stale (EFRIS returns code 15 "Data decryption error"
or code 99), we automatically run T103+T104 to refresh the session and retry
once — same as SAP B1's automatic re-login behaviour.

Scheduler: runs daily at midnight (see hooks.py scheduler_events)
Manual:    EFRIS Settings → "Fetch URA Exchange Rates" button
"""

from datetime import date, datetime, timedelta

import frappe

from efris_integration.api.base import EFRISClient, EFRISAPIError

# EFRIS error codes that mean stale session / bad key — trigger auto-refresh
# 15 = Data decryption error (stale AES key)
# 39 = Signature value can not be empty (private key missing or unreadable)
# 16/99/03 = generic session / auth failures
_SESSION_EXPIRED_CODES = {"15", "16", "39", "99", "03"}


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
        {"synced": N, "date": "YYYY-MM-DD"}
    """
    today_str = date.today().isoformat()
    from_date = from_date or today_str
    to_date   = to_date   or today_str

    try:
        d_from = date.fromisoformat(from_date)
        d_to   = date.fromisoformat(to_date)
    except ValueError as exc:
        frappe.throw(f"Invalid date format: {exc}")

    client      = EFRISClient()
    total_saved = 0
    last_date   = today_str

    current = d_from
    while current <= d_to:
        saved, fetched_date = _fetch_and_upsert(client, current)
        total_saved += saved
        last_date    = fetched_date or current.isoformat()
        current     += timedelta(days=1)

    frappe.db.commit()
    frappe.msgprint(
        f"✅ {total_saved} exchange rate(s) updated for {last_date}.",
        indicator="green",
    )
    return {"synced": total_saved, "date": last_date}


# ─── Internal ─────────────────────────────────────────────────────────────────

def _fetch_and_upsert(client: EFRISClient, query_date: date) -> tuple[int, str]:
    """
    Send T126 for query_date, parse T120 list, upsert rates.
    Auto-refreshes the session (T103+T104) once if the key is stale.
    Returns (count_saved, date_string).
    """
    query_date_str = query_date.strftime("%Y-%m-%d") + " 00:00:00"
    payload = {"queryDate": query_date_str}

    result = _call_t126(client, payload, query_date_str, allow_refresh=True)

    rate_list = _extract_rate_list(result)
    if not rate_list:
        frappe.logger("efris").warning(
            f"T126: no rates returned for {query_date_str}. Raw: {result}"
        )
        return 0, query_date.isoformat()

    date_str = query_date.isoformat()
    saved    = 0
    now_time = ""

    for item in rate_list:
        currency = (item.get("currency") or "").strip().upper()
        rate_val = item.get("rate")
        now_time = item.get("nowTime") or now_time

        if not currency or rate_val is None:
            continue
        if currency == "UGX":          # skip base currency
            continue

        try:
            rate_float = float(rate_val)
        except (TypeError, ValueError):
            continue

        if rate_float <= 0:
            continue

        _upsert_currency_exchange(currency, rate_float, date_str)
        saved += 1

    canonical_date = _parse_now_time(now_time) or date_str
    return saved, canonical_date


def _call_t126(client: EFRISClient, payload: dict,
               query_date_str: str, allow_refresh: bool) -> dict:
    """
    POST T126 with AES-encrypted payload (no signing — SAP B1 doesn't sign T126).
    If EFRIS returns a session-expired code and allow_refresh=True,
    silently refresh T103+T104 and retry once.
    """
    try:
        return client.call(
            "T126",
            payload=payload,
            encrypt=True,
            sign=True,           # EFRIS requires signature — error [39] if absent
            doc_type="Currency Exchange",
            doc_name="",
        )

    except EFRISAPIError as exc:
        if exc.code in _SESSION_EXPIRED_CODES and allow_refresh:
            frappe.logger("efris").info(
                f"T126: session error [{exc.code}] — auto-refreshing T103+T104 and retrying."
            )
            _refresh_session(client)
            # Retry once with a fresh session — do NOT allow another refresh loop
            return _call_t126(client, payload, query_date_str, allow_refresh=False)

        # Non-session error or refresh already attempted — surface it clearly
        frappe.log_error(
            f"T126 failed for {query_date_str}: [{exc.code}] {exc.message}",
            "EFRIS Exchange Rate Error",
        )
        frappe.throw(
            f"❌ EFRIS exchange rate (T126) failed: <b>[{exc.code}]</b> {exc.message}",
            title="Exchange Rate Error",
        )

    except Exception as exc:
        # Session key padding / decryption error on our side → need Full Initialise
        if "padding" in str(exc).lower() or "decryption" in str(exc).lower():
            if allow_refresh:
                frappe.logger("efris").info(
                    "T126: local AES padding error — auto-refreshing T103+T104."
                )
                _refresh_session(client)
                return _call_t126(client, payload, query_date_str, allow_refresh=False)
        raise


def _refresh_session(client: EFRISClient):
    """
    Run T103 (sign-in) + T104 (new AES key) to renew the session.
    Mirrors SAP B1's automatic re-login on session expiry.
    Raises frappe.ValidationError with a clear message if refresh fails.
    """
    try:
        from efris_integration.api.auth import sign_in, get_symmetric_key
        sign_in()
        get_symmetric_key()
        # Reset the cached AES key so the next call picks up the fresh one
        client._aes_key = None
        frappe.logger("efris").info("T126: session refreshed successfully via T103+T104.")
    except Exception as exc:
        frappe.throw(
            "⚠️ EFRIS session key has expired and automatic refresh failed.<br><br>"
            f"Refresh error: {exc}<br><br>"
            "Please go to <b>EFRIS Settings → Initialise → 🔑 Full Initialise (T101→T104)</b> "
            "to manually obtain a fresh session key, then try again.",
            title="Session Expired — Manual Initialise Required",
        )


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _extract_rate_list(result) -> list:
    """
    T126 response data may arrive as a list or wrapped in a dict.
    """
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


def _upsert_currency_exchange(currency: str, rate: float, date_str: str):
    """Insert or update a Currency Exchange record (from_currency → UGX)."""
    existing = frappe.db.get_value(
        "Currency Exchange",
        {"from_currency": currency, "to_currency": "UGX", "date": date_str},
        "name",
    )
    if existing:
        frappe.db.set_value("Currency Exchange", existing, "exchange_rate", rate)
    else:
        _ensure_currency(currency)
        frappe.get_doc({
            "doctype":       "Currency Exchange",
            "from_currency": currency,
            "to_currency":   "UGX",
            "date":          date_str,
            "exchange_rate": rate,
            "for_buying":    1,
            "for_selling":   1,
        }).insert(ignore_permissions=True)


def _ensure_currency(currency: str):
    """Create the Currency master if it doesn't already exist."""
    if not frappe.db.exists("Currency", currency):
        try:
            frappe.get_doc({
                "doctype":       "Currency",
                "currency_name": currency,
                "enabled":       1,
            }).insert(ignore_permissions=True)
        except Exception:
            pass


def _parse_now_time(now_time: str) -> str | None:
    """Parse T120.nowTime → "YYYY-MM-DD"."""
    if not now_time:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(now_time.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None
