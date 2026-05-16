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
    _ensure_session(client, company)

    total_inserted = 0
    total_updated  = 0
    failed_dates   = []
    current        = d_from

    days_total = (d_to - d_from).days + 1

    while current <= d_to:
        date_str     = current.isoformat()           # "yyyy-MM-dd"
        query_ts     = date_str + " 00:00:00"        # "yyyy-MM-dd 00:00:00"
        erpnext_date = current.strftime("%Y-%m-%d")

        try:
            rate_list = _fetch_rates_for_date(client, company, query_ts, date_str)
        except EFRISAPIError as exc:
            # Non-00 returnCode for this date — log and continue (matches SAP B1)
            frappe.log_error(
                f"T126 [{exc.code}] {exc.message} for queryDate={query_ts} company={company}",
                "EFRIS Exchange Rate Skip",
            )
            frappe.logger("efris").warning(
                f"T126 skip {date_str} [{company}]: [{exc.code}] {exc.message}"
            )
            failed_dates.append(date_str)
            current += timedelta(days=1)
            continue
        except Exception as exc:
            frappe.log_error(
                f"T126 unexpected error for queryDate={query_ts} company={company}: {exc}",
                "EFRIS Exchange Rate Error",
            )
            failed_dates.append(date_str)
            current += timedelta(days=1)
            continue

        if not rate_list:
            frappe.logger("efris").warning(
                f"T126: no rates returned for {date_str} [{company}]"
            )
            current += timedelta(days=1)
            continue

        # Log the first item of each day's response so we can see the exact
        # field names EFRIS returns (check Error Log → "EFRIS T126 Rate Item Shape")
        if rate_list:
            frappe.log_error(
                f"T126 [{company}] {date_str}: {len(rate_list)} items.\n"
                f"First item keys: {list(rate_list[0].keys()) if isinstance(rate_list[0], dict) else rate_list[0]}\n"
                f"First item: {rate_list[0]}",
                "EFRIS T126 Rate Item Shape",
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

            try:
                rate_float = float(rate_val)
            except (TypeError, ValueError):
                continue
            if rate_float <= 0:
                continue

            inserted = _upsert_rate(currency, rate_float, erpnext_date)
            if inserted:
                total_inserted += 1
            else:
                total_updated += 1

        current += timedelta(days=1)

    frappe.db.commit()

    # Build result message — mirrors SAP B1 ShowSuccessMessage
    days_done = days_total - len(failed_dates)
    msg = (
        f"✅ URA rates updated for {days_done}/{days_total} day(s): "
        f"{total_inserted} added, {total_updated} updated."
    )
    if failed_dates:
        msg += f"<br>⚠️ Skipped {len(failed_dates)} day(s): {', '.join(failed_dates)}"

    frappe.msgprint(msg, indicator="green" if not failed_dates else "orange")
    return {
        "inserted": total_inserted,
        "updated":  total_updated,
        "failed":   failed_dates,
        "days":     days_done,
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
    Call T126 for a single date.  Mirrors the inner loop body of
    FetchAndUpdateRatesForRange() in ExchangeRateForm.cs.

    Payload:   {"queryDate":"yyyy-MM-dd 00:00:00"}
    Encrypted: AES-128-ECB with session AES key  (encryptCode=2, zipCode=0)
    Signed:    RSA SHA1 PKCS1v1.5 of base64(encrypted content)

    Auto-refreshes the session once on SESSION_EXPIRED and retries.
    """
    try:
        result = _call_t126_for_date(client, query_ts, date_label)
    except EFRISAPIError as exc:
        if exc.code not in ("SESSION_EXPIRED", "15", "16", "99"):
            raise   # real EFRIS error — let caller handle
        # Session expired → refresh and retry once
        frappe.logger("efris").info(
            f"T126 [{exc.code}] for {date_label} [{company}] — refreshing session and retrying."
        )
        _refresh_session(client, company)
        result = _call_t126_for_date(client, query_ts, date_label)   # may raise again

    return _extract_rate_list(result)


def _call_t126_for_date(client: EFRISClient, query_ts: str, date_label: str) -> dict:
    """
    POST T126 with AES-encrypted + RSA-signed queryDate payload.

    Exactly matches ExchangeRateForm.cs:
        queryJson  = '{"queryDate":"' + queryDate + '"}'
        contentB64 = Base64(AES.Encrypt(UTF8(queryJson), aesKey))
        signature  = Base64(RSA.SignData(UTF8(contentB64), SHA1, PKCS1))
        body.Data.Content   = contentB64
        body.Data.Signature = signature
    """
    return client.call(
        "T126",
        payload={"queryDate": query_ts},
        encrypt=True,   # AES-ECB encrypt with session key  → encryptCode=2
        sign=True,      # RSA SHA1 PKCS1 signature of contentB64
        doc_type="Currency Exchange",
        doc_name=date_label,
    )


# ─── Session helpers ───────────────────────────────────────────────────────────

def _ensure_session(client: EFRISClient, company: str):
    """
    Verify the current session key is usable before starting the rate loop.
    Mirrors SAP B1's CommonValues.GetUraData() at the start of FetchAndUpdateRatesForRange.
    """
    existing_key = None
    try:
        existing_key = client.key_manager.get_aes_key()
    except Exception:
        pass

    if existing_key:
        client._aes_key = existing_key
        try:
            client.call(
                "T126", payload=None, encrypt=False,
                doc_type="__session_check__", doc_name="pre-sync",
            )
            return   # key is good
        except EFRISAPIError as exc:
            if exc.code != "SESSION_EXPIRED":
                return   # decrypt succeeded; EFRIS returned other code — key is fine
        except Exception:
            return   # network issue — proceed optimistically
        # SESSION_EXPIRED → fall through to refresh
        client._aes_key = None

    _refresh_session(client, company)


def _refresh_session(client: EFRISClient, company: str):
    """
    Renew the EFRIS session via T104 (checks existing key first).
    Forces a reload of the AES key from file after T104 completes.
    """
    from efris_integration.api.auth import get_symmetric_key
    get_symmetric_key(company)
    client._aes_key = None   # force reload from SessionAESKey.txt on next call


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
    existing = frappe.db.get_value(
        "Currency Exchange",
        {"from_currency": currency, "to_currency": "UGX", "date": date_str},
        "name",
    )
    if existing:
        frappe.db.set_value("Currency Exchange", existing, "exchange_rate", rate)
        return False  # updated

    # Ensure the currency exists in ERPNext
    if not frappe.db.exists("Currency", currency):
        try:
            frappe.get_doc({
                "doctype":       "Currency",
                "currency_name": currency,
                "enabled":       1,
            }).insert(ignore_permissions=True)
        except Exception:
            pass   # might already exist due to race; non-fatal

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
