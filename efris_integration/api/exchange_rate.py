"""
T126 — Fetch URA Exchange Rates
T120 — Exchange Rate model

Mirrors: URASetting.cs → FetchAndUpdateExchangeRates (EFRIS Settings screen)

SAP B1 flow (URASetting.cs):
  1. Build request with EMPTY data block — NO content, NO signature, NO queryDate
  2. POST T126 with InterfaceCode = "T126"
  3. Response is AES-encrypted (encryptCode=2) → decrypt with session AES key
  4. Parse list of T120 objects
  5. T120 fields (exact lowercase): currency, rate, nowTime
  6. Skip UGX (local base currency)
  7. Upsert into ERPNext Currency Exchange (from_currency → UGX)

Note: ExchangeRateForm.cs (a separate screen) sends an encrypted queryDate.
      URASetting.cs (the EFRIS Settings screen, which is what we implement here)
      sends NO payload at all — just the global envelope.
"""

from datetime import date, datetime, timedelta

import frappe

from efris_integration.api.base import EFRISClient, EFRISAPIError


@frappe.whitelist()
def sync_exchange_rates(from_date: str = None, to_date: str = None) -> dict:
    """
    Fetch EFRIS exchange rates and upsert into ERPNext Currency Exchange.
    Called by the "Fetch URA Exchange Rates" button and daily scheduler.

    T126 sends NO payload (URASetting.cs behaviour) — EFRIS returns
    current-day rates.  A single T126 call is made; the returned rates
    are stored against every date in the from_date → to_date range.
    """
    today_str = date.today().isoformat()
    from_date = from_date or today_str
    to_date   = to_date   or today_str

    try:
        d_from = date.fromisoformat(from_date)
        d_to   = date.fromisoformat(to_date)
    except ValueError as exc:
        frappe.throw(f"Invalid date: {exc}")

    client = EFRISClient()

    # ── Single T126 call — no payload, no queryDate (URASetting.cs) ──────
    rate_list = _fetch_rates(client)

    if not rate_list:
        frappe.msgprint("⚠️ T126 returned no exchange rates.", indicator="orange")
        return {"synced": 0, "date": today_str}

    # ── Upsert rates for every date in the requested range ────────────────
    total_saved = 0
    now_time    = ""
    current     = d_from

    while current <= d_to:
        date_str = current.isoformat()
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
            total_saved += 1

        current += timedelta(days=1)

    display_date = _parse_date(now_time) or today_str
    frappe.db.commit()
    frappe.msgprint(
        f"✅ {total_saved} exchange rate(s) updated for {display_date}.",
        indicator="green",
    )
    return {"synced": total_saved, "date": display_date}


def _fetch_rates(client: EFRISClient) -> list:
    """
    Call T126 once (empty payload) and return the raw T120 list.
    Auto-refreshes the session once if the AES key is stale (code 15/16/99).
    """
    try:
        result = _call_t126(client)
    except EFRISAPIError as exc:
        if exc.code in ("15", "16", "99", "SESSION_EXPIRED"):
            frappe.logger("efris").info(
                f"T126 [{exc.code}] — refreshing session (T103+T104) and retrying."
            )
            # Let T104 errors (e.g. private key not configured) propagate
            # directly so the user sees the real cause, not SESSION_EXPIRED.
            _refresh_session(client)
            try:
                result = _call_t126(client)
            except EFRISAPIError as exc2:
                frappe.log_error(
                    f"T126 [{exc2.code}] {exc2.message} after session refresh",
                    "EFRIS Exchange Rate Error",
                )
                frappe.throw(
                    f"❌ T126 still failing after session refresh: [{exc2.code}] {exc2.message}",
                    title="Exchange Rate Error",
                )
        else:
            frappe.log_error(
                f"T126 [{exc.code}] {exc.message}",
                "EFRIS Exchange Rate Error",
            )
            frappe.throw(f"❌ T126 failed: [{exc.code}] {exc.message}", title="Exchange Rate Error")

    rate_list = _extract_rate_list(result)
    if not rate_list:
        frappe.logger("efris").warning(f"T126: no rates in response. Raw: {result}")
    return rate_list


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _call_t126(client: EFRISClient) -> dict:
    """
    POST T126 with an empty data block — mirrors URASetting.cs exactly.

    SAP B1 URASetting.cs FetchAndUpdateExchangeRates() sends T126 with
    NO payload, NO content, NO signature.  The server returns today's
    exchange rates regardless; the queryDate parameter is not used in
    this call path.

    The response comes back AES-encrypted (encryptCode=2) and is
    decrypted automatically by EFRISClient.call().
    """
    return client.call(
        "T126",
        payload=None,       # empty data block — matches URASetting.cs
        encrypt=False,      # nothing to encrypt
        sign=False,         # no signature
        doc_type="Currency Exchange",
        doc_name="",
    )


def _refresh_session(client: EFRISClient):
    """
    Renew the EFRIS session: T103 (sign-in) → T104 (new AES session key).
    Only the session AES key expires — the private key (T102) stays valid.
    Resets the cached AES key so the next T126 call reads the fresh key from file.
    """
    from efris_integration.api.auth import sign_in, get_symmetric_key
    # URA guide order: T104 first (get AES key), then T103 (sign in)
    get_symmetric_key() # T104 — new session AES key
    sign_in()           # T103 — sign in
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
