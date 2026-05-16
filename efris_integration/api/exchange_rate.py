"""
T126 — Fetch URA Exchange Rates  (per-company)
T120 — Exchange Rate model

Mirrors EXACTLY: ExchangeRateForm.cs → FetchAndUpdateRatesForRange()

SAP B1 flow (ExchangeRateForm.cs):
  1. Loop day-by-day from fromDate to toDate.
  2. For each day build payload: {"queryDate":"yyyy-MM-dd 00:00:00"}
  3. AES-ECB encrypt the JSON using the session AES key.
  4. Sign the base64(encrypted) string with RSA SHA1 PKCS1.
  5. POST T126 with Content=contentB64 + Signature.
  6. If returnCode != "00" → log and continue to next day.
  7. AES-decrypt the response → parse List<T120>.
  8. T120 fields: currency, rate, nowTime.
  9. Skip UGX.  Upsert into ORTT (ERPNext: Currency Exchange).

All entry points require a `company` argument — EFRIS Settings are per-company.
The scheduler wrapper `sync_exchange_rates_all_companies()` iterates every
configured company automatically.
"""

from datetime import date, datetime, timedelta

import frappe

from efris_integration.api.base import EFRISClient, EFRISAPIError
from efris_integration.api.session import (
    SESSION_ERRORS,
    ensure_session,
    refresh_session,
    throw_session_key_error,
)


@frappe.whitelist()
def sync_exchange_rates(
    company: str,
    from_date: str = None,
    to_date: str = None,
) -> dict:
    """
    Fetch EFRIS exchange rates for a company and upsert into ERPNext Currency Exchange.

    Mirrors ExchangeRateForm.cs → FetchAndUpdateRatesForRange() exactly:
      • One T126 call per calendar day in [from_date, to_date]
      • Payload = AES-encrypted {"queryDate":"yyyy-MM-dd 00:00:00"} + RSA-SHA1 signature
      • Failed days are skipped (logged); the loop continues
    """
    if not company:
        frappe.throw("Company is required for exchange rate sync.")

    today_str = date.today().isoformat()
    from_date = from_date or today_str
    to_date   = to_date   or today_str

    try:
        d_from = date.fromisoformat(from_date)
        d_to   = date.fromisoformat(to_date)
    except ValueError as exc:
        frappe.throw(f"Invalid date: {exc}")

    if d_from > d_to:
        frappe.throw("From Date must not be after To Date.")

    client = EFRISClient(company=company)

    # Ensure we have a valid session key before starting the loop.
    # This mirrors SAP B1's GetUraData() / CommonValues.GetUraData() call at the top.
    ensure_session(client, company)

    # Load all ERPNext currencies that exist AND are enabled (Status = Enabled).
    # Only these will receive rates — currencies that are missing or disabled are skipped.
    # Never auto-create currencies; the user controls what's in the Currency list.
    erpnext_currencies: set[str] = set(
        frappe.get_all("Currency", filters={"enabled": 1}, pluck="name")
    )
    erpnext_currencies.discard("UGX")  # UGX is always the target currency; never fetch its own rate
    frappe.logger("efris").info(
        f"[{company}] T126: will save rates only for ERPNext currencies: {sorted(erpnext_currencies)}"
    )

    total_inserted = 0
    total_updated  = 0
    # Each entry: (date_str, error_code) so we can show reasons in the result
    failed_dates: list[tuple[str, str]] = []
    # Collect every saved rate: (date, currency, rate_float) for the result dialog
    saved_rates: list[tuple[str, str, float]] = []
    current        = d_from

    days_total = (d_to - d_from).days + 1

    while current <= d_to:
        date_str     = current.isoformat()           # "yyyy-MM-dd"
        query_ts     = date_str + " 00:00:00"        # T126 queryDate format per ExchangeRateForm.cs
        erpnext_date = current.strftime("%Y-%m-%d")

        try:
            rate_list = _fetch_rates_for_date(client, company, query_ts, date_str)
        except EFRISAPIError as exc:
            # Non-00 returnCode for this date — log and continue (mirrors SAP B1)
            short_msg = (
                f"T126 [{exc.code}] {date_str} [{company}]: {exc.message}"
            )[:500]
            frappe.log_error(title="EFRIS Exchange Rate Skip", message=short_msg)
            frappe.logger("efris").warning(short_msg)
            failed_dates.append((date_str, exc.code))
            current += timedelta(days=1)
            continue
        except Exception as exc:
            short_msg = f"T126 error {date_str} [{company}]: {str(exc)[:400]}"
            frappe.log_error(title="EFRIS Exchange Rate Error", message=short_msg)
            frappe.logger("efris").error(short_msg)
            failed_dates.append((date_str, "ERR"))
            current += timedelta(days=1)
            continue

        if not rate_list:
            frappe.logger("efris").warning(
                f"T126: no rates returned for {date_str} [{company}]"
            )
            failed_dates.append((date_str, "EMPTY"))
            current += timedelta(days=1)
            continue

        # Log rate item shape to frappe logger (not Error Log) for debugging
        frappe.logger("efris").info(
            f"T126 [{company}] {date_str}: {len(rate_list)} items. "
            f"First: {rate_list[0] if rate_list else '—'}"
        )

        for item in rate_list:
            if not isinstance(item, dict):
                continue

            # Try every field name variant EFRIS may use for the currency code
            currency = (
                item.get("currency")
                or item.get("currencyCode")
                or item.get("Currency")
                or item.get("CurrencyCode")
                or ""
            )
            currency = str(currency).strip().upper()

            # Try every field name variant for the exchange rate value
            rate_val = (
                item.get("rate")
                or item.get("exchangeRate")
                or item.get("Rate")
                or item.get("ExchangeRate")
                or item.get("rateValue")
                or item.get("midRate")
            )

            if not currency or rate_val is None:
                continue
            if currency in ("UGX", ""):
                continue

            # Skip currencies not configured in ERPNext — do NOT auto-create.
            if currency not in erpnext_currencies:
                frappe.logger("efris").info(
                    f"T126 [{company}] {date_str}: skipping {currency} — not in ERPNext Currency list"
                )
                continue

            try:
                rate_float = float(rate_val)
            except (TypeError, ValueError):
                continue
            if rate_float <= 0:
                continue

            inserted = _upsert_rate(currency, rate_float, erpnext_date)
            saved_rates.append((erpnext_date, currency, rate_float))
            if inserted:
                total_inserted += 1
            else:
                total_updated += 1

        current += timedelta(days=1)

    frappe.db.commit()

    # ── Build result message ───────────────────────────────────────────────
    days_done = days_total - len(failed_dates)
    msg = (
        f"<b>URA Exchange Rates — {company}</b><br>"
        f"Period: {from_date} → {to_date}<br><br>"
    )

    # ── Rates table ────────────────────────────────────────────────────────
    if saved_rates:
        # Group by date for clean display
        from collections import defaultdict
        by_date: dict = defaultdict(list)
        for ds, cur, rt in saved_rates:
            by_date[ds].append((cur, rt))

        for ds in sorted(by_date.keys()):
            msg += f"<b>{ds}</b><br>"
            msg += "<table style='width:100%;border-collapse:collapse;margin-bottom:8px'>"
            msg += (
                "<tr style='background:#f5f5f5'>"
                "<th style='text-align:left;padding:4px 8px'>Currency</th>"
                "<th style='text-align:right;padding:4px 8px'>Rate (1 unit → UGX)</th>"
                "<th style='text-align:center;padding:4px 8px'>Status</th>"
                "</tr>"
            )
            for cur, rt in sorted(by_date[ds]):
                msg += (
                    f"<tr style='border-top:1px solid #eee'>"
                    f"<td style='padding:4px 8px'><b>{cur}</b></td>"
                    f"<td style='text-align:right;padding:4px 8px'>{rt:,.2f}</td>"
                    f"<td style='text-align:center;padding:4px 8px'>✅</td>"
                    f"</tr>"
                )
            msg += "</table>"

        msg += f"<b>{total_inserted}</b> added, <b>{total_updated}</b> updated.<br>"
    else:
        msg += "No rates were saved.<br>"

    # ── Skipped dates ──────────────────────────────────────────────────────
    if failed_dates:
        skip_lines = []
        session_key_error = False
        for ds, code in failed_dates:
            skip_lines.append(f"{ds} [{code}]")
            if code in ("15", "16", "SESSION_EXPIRED", "SESSION_REFRESH_FAILED"):
                session_key_error = True

        msg += f"<br>⚠️ Skipped {len(failed_dates)} day(s): {', '.join(skip_lines)}"

        if session_key_error:
            msg += (
                "<br><br>❌ <b>Session key issue (error 15)</b> — EFRIS cannot decrypt "
                "the request because the Session AES Key is stale.<br>"
                "Fix: paste the current <b>SessionAESKey.txt</b> content from the "
                "SAP B1 server into <b>EFRIS Settings → Session AES Key</b> and save."
            )
        elif any(c == "EMPTY" for _, c in failed_dates):
            msg += (
                "<br><br>ℹ️ EFRIS returned no rates for these dates. "
                "URA may not publish today's rates until end of business day — "
                "try fetching for yesterday."
            )

    frappe.msgprint(
        msg,
        title="URA Exchange Rates",
        indicator="green" if not failed_dates else "orange",
    )
    return {
        "inserted": total_inserted,
        "updated":  total_updated,
        "failed":   [ds for ds, _ in failed_dates],
        "days":     days_done,
        "rates":    [{"date": ds, "currency": cur, "rate": rt} for ds, cur, rt in saved_rates],
    }


def sync_exchange_rates_all_companies(from_date: str = None, to_date: str = None):
    """
    Scheduler entry point — runs sync_exchange_rates for EVERY configured company.
    Called daily via hooks.py scheduler_events.
    Errors in one company do not stop other companies from running.
    """
    companies = frappe.get_all(
        "EFRIS Settings",
        filters={"api_url": ["!=", ""]},
        fields=["name"],
    )
    for row in companies:
        company = row["name"]
        try:
            frappe.logger("efris").info(
                f"Daily exchange rate sync — starting for [{company}]"
            )
            sync_exchange_rates(company=company, from_date=from_date, to_date=to_date)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"EFRIS Daily Exchange Rate Sync Failed [{company}]",
            )


# ─── Core per-day fetch ────────────────────────────────────────────────────────

def _fetch_rates_for_date(
    client: EFRISClient,
    company: str,
    query_ts: str,
    date_label: str,
) -> list:
    """
    Call T126 for a single date.

    ExchangeRateForm.cs (the working C# source):
        string queryDate = date.ToString("yyyy-MM-dd") + " 00:00:00";
        string queryJson = "{\"queryDate\":\"" + queryDate + "\"}";
        → payload: {"queryDate": "yyyy-MM-dd 00:00:00"}

    Encrypted: AES-128-ECB with session AES key  (encryptCode=2, zipCode=0)
    Signed:    RSA SHA1 PKCS1v1.5 of base64(encrypted content)

    EFRIS error codes that mean "session expired / wrong AES key":
      15 = Data decryption error  (EFRIS can't decrypt our request — our key is stale)
      16 = Signature verification error
      99 = General session error
    On these codes, refresh the session once and retry.
    """
    try:
        result = _call_t126_for_date(client, query_ts, date_label)
    except EFRISAPIError as exc:
        # Codes that mean our AES session key is wrong/stale:
        #   "15"   — EFRIS PKCS7 unpad failed (definitely wrong key)
        #   "2066" — "Illegal json format": EFRIS decrypted to garbage (wrong key,
        #            PKCS7 happened to pass but result isn't valid JSON)
        #   "16"   — Signature verification error (session mismatch)
        #   "99"   — General session error
        if exc.code not in SESSION_ERRORS:
            raise   # real EFRIS business error — let caller handle

        # Session expired → refresh and retry once
        frappe.logger("efris").info(
            f"T126 [{exc.code}] '{exc.message}' for {date_label} [{company}] "
            "— session stale, attempting T104 refresh."
        )
        try:
            refresh_session(client, company)
        except Exception as refresh_exc:
            # T104 failed (private key wrong/empty). Convert to a clean,
            # short EFRISAPIError so the outer loop can log it without
            # hitting MySQL column length limits.
            short = str(refresh_exc)[:300]
            raise EFRISAPIError(
                "SESSION_REFRESH_FAILED",
                f"T104 session refresh failed — paste Session AES Key in EFRIS Settings. "
                f"Detail: {short}",
            ) from refresh_exc

        result = _call_t126_for_date(client, query_ts, date_label)   # may raise again

    return _extract_rate_list(result)


def _call_t126_for_date(client: EFRISClient, query_ts: str, date_label: str) -> dict:
    """
    POST T126 with AES-encrypted + RSA-signed queryDate payload.

    Mirrors ExchangeRateForm.cs exactly:
        string queryDate = date.ToString("yyyy-MM-dd") + " 00:00:00";
        string queryJson = "{\"queryDate\":\"" + queryDate + "\"}";
        contentB64 = Base64(AES.Encrypt(UTF8(queryJson), aesKey))
        signature  = Base64(RSA.SignData(UTF8(contentB64), SHA1, PKCS1))

    T120 response fields: currency, rate, nowTime
    """
    return client.call(
        "T126",
        payload={"queryDate": query_ts},     # "yyyy-MM-dd 00:00:00"
        encrypt=True,   # AES-ECB encrypt with session key  → encryptCode=2
        sign=True,      # RSA SHA1 PKCS1 signature of contentB64
        doc_type="Currency Exchange",
        doc_name=date_label,
    )


# ─── Session helpers — delegated to efris_integration.api.session ─────────────
# ensure_session() and refresh_session() are imported at the top of this file.
# This module calls them directly; no local wrappers needed.


# ─── Response parsing ─────────────────────────────────────────────────────────

def _extract_rate_list(result) -> list:
    """
    Extract the List<T120> from the decrypted T126 response.

    EFRIS can return the rate list in several forms:
      • Directly as a JSON array                   → [{"currency":"USD","rate":"3850"}]
      • Wrapped in a dict with a known key         → {"data":[...]}
      • Wrapped with an unknown key                → {"someKey":[...]}
      • Single item dict (only one currency)       → {"currency":"USD","rate":"3850"}
    """
    if isinstance(result, list):
        # Direct array — most common
        # Flatten if it's a list-of-lists (rare)
        if result and isinstance(result[0], list):
            flat = []
            for sub in result:
                flat.extend(sub)
            return flat
        return result

    if isinstance(result, dict):
        # Known wrapper keys (try all variants)
        for key in ("data", "rates", "exchangeRates", "rateList",
                    "Data", "Rates", "ExchangeRates", "RateList"):
            val = result.get(key)
            if isinstance(val, list):
                return val

        # Any list value in the dict
        for val in result.values():
            if isinstance(val, list) and val:
                return val

        # Dict is itself one rate record (single currency response)
        if "currency" in result or "currencyCode" in result:
            return [result]

    return []


# ─── ERPNext upsert (mirrors ORTT INSERT/UPDATE in SAP B1) ───────────────────

def _upsert_rate(currency: str, rate: float, date_str: str) -> bool:
    """
    Insert or update a Currency Exchange record.
    Returns True if inserted, False if updated.
    Mirrors SAP B1:
        SELECT COUNT(*) FROM ORTT WHERE Currency=? AND RateDate=?
        → UPDATE if exists, INSERT if not
    """
    # Guard: only save rates for currencies that exist AND are enabled in ERPNext.
    # The main loop already filters by erpnext_currencies (enabled=1), but this
    # protects against any direct calls to _upsert_rate that bypass that check.
    currency_enabled = frappe.db.get_value("Currency", currency, "enabled")
    if not currency_enabled:
        frappe.logger("efris").info(
            f"_upsert_rate: skipping {currency} for {date_str} — "
            f"{'Currency disabled in ERPNext' if currency_enabled == 0 else 'Currency not in ERPNext'}"
        )
        return False

    existing = frappe.db.get_value(
        "Currency Exchange",
        {"from_currency": currency, "to_currency": "UGX", "date": date_str},
        "name",
    )
    if existing:
        frappe.db.set_value("Currency Exchange", existing, "exchange_rate", rate)
        return False  # updated

    frappe.get_doc({
        "doctype":       "Currency Exchange",
        "from_currency": currency,
        "to_currency":   "UGX",
        "date":          date_str,
        "exchange_rate": rate,
        "for_buying":    1,
        "for_selling":   1,
    }).insert(ignore_permissions=True)
    return True  # inserted


# ─── Date parsing ─────────────────────────────────────────────────────────────

def _parse_date(val: str) -> str | None:
    if not val:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(val.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None
