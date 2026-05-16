"""
Shared EFRIS session management — used by all T-code modules.

Global AES key cache (Redis-backed via frappe.cache):
─────────────────────────────────────────────────────
Mirrors SAP B1's SessionAESKey.txt pattern:
  • SAP B1 reads SessionAESKey.txt ONCE and reuses it for every operation.
  • Here we store the key in Redis (frappe.cache) so all modules, workers,
    and requests share the same key — no per-module DB reads on every call.
  • DB (EFRIS Settings.session_aes_key) remains the source of truth.
    Redis is a fast, shared read-through cache on top of it.

Design rules:
  • Load key from Redis cache → fallback to DB → cache it.
  • T103 is NEVER called from session helpers. It resets the EFRIS server
    session and invalidates the SAP B1 key if T104 fails afterwards.
  • T104 only for initialisation/refresh.
  • On session error mid-operation: clear cache + throw actionable error.
    Do NOT auto-retry — let the user fix the key and retry manually.
    (This matches SAP B1: if SessionAESKey.txt is wrong, it shows an error.)
"""

import frappe

from efris_integration.api.base import EFRISClient

# ─── Error code constants ─────────────────────────────────────────────────────

SESSION_ERRORS: frozenset[str] = frozenset({
    "15",             # Data decryption error  (PKCS7 unpad failed — wrong key)
    "2066",           # Illegal json format    (PKCS7 passed but result is garbage)
    "16",             # Signature verify error (session/key mismatch)
    "99",             # General session error
    "SESSION_EXPIRED",# We couldn't decrypt EFRIS's response (wrong key on our side)
})

# Superset used by auth.py T104 candidate probing
WRONG_KEY_CODES: frozenset[str] = SESSION_ERRORS | frozenset({"9999", "9998"})


# ─── Global AES key cache (Redis) ─────────────────────────────────────────────

def _cache_key(company: str) -> str:
    return f"efris:aes_key:{frappe.scrub(company)}"


def get_global_aes_key(company: str) -> "bytes | None":
    """
    Return the session AES key from the global Redis cache.
    Returns None if not cached — caller should load from DB and call set_global_aes_key().
    """
    try:
        val = frappe.cache().get_value(_cache_key(company))
        if isinstance(val, bytes) and len(val) in (16, 24, 32):
            return val
    except Exception:
        pass
    return None


def set_global_aes_key(company: str, key_bytes: bytes) -> None:
    """
    Store the session AES key in the global Redis cache (TTL: 24 h).
    Called after loading from DB or after a successful T104 refresh.
    """
    try:
        frappe.cache().set_value(_cache_key(company), key_bytes, expires_in_sec=86400)
    except Exception:
        pass  # cache failure is non-fatal — DB is the source of truth


def clear_global_aes_key(company: str) -> None:
    """
    Invalidate the Redis cache for this company.
    Called when the key is known to be stale (session error received).
    The next ensure_session() will reload from DB.
    """
    try:
        frappe.cache().delete_value(_cache_key(company))
    except Exception:
        pass


# ─── Session helpers ──────────────────────────────────────────────────────────

def ensure_session(client: EFRISClient, company: str) -> None:
    """
    Load the session AES key into the client before the first T-code call.

    Lookup order (mirrors SAP B1 reading SessionAESKey.txt once):
      1. Global Redis cache   — shared across all modules, workers, requests
      2. EFRIS Settings DB    — source of truth; result cached after first hit
      3. T104 (first-run only)— when no key is stored anywhere

    Never calls T103. Never auto-refreshes if the key looks stale.
    """
    # 1. Global cache hit
    cached = get_global_aes_key(company)
    if cached:
        client._aes_key = cached
        frappe.logger("efris").info(
            f"[{company}] AES key from global cache ({len(cached) * 8}-bit)"
        )
        return

    # 2. Load from DB and populate cache
    try:
        key = client.key_manager.get_aes_key()
        client._aes_key = key
        set_global_aes_key(company, key)
        frappe.logger("efris").info(
            f"[{company}] AES key loaded from DB ({len(key) * 8}-bit) → cached"
        )
        return
    except RuntimeError:
        pass  # no key stored at all

    # 3. First-run: T104 to initialise
    # refresh_session() reloads client.settings and client._km from DB after T104,
    # and sets client._aes_key + global cache — nothing more needed here.
    frappe.logger("efris").info(
        f"[{company}] no session key stored — running T104 to initialise"
    )
    refresh_session(client, company)


def refresh_session(client: EFRISClient, company: str) -> None:
    """
    Fetch a fresh session AES key via T104 and update both DB and global cache.

    Critical: T104 uses its OWN EFRISClient instance internally to save the new key.
    The caller's client.settings is a DIFFERENT in-memory object — it still holds
    the OLD session_aes_key.  We must reload from DB after T104, not from the
    stale in-memory settings, or the retry will encrypt with the old key again.
    """
    from efris_integration.api.auth import get_symmetric_key

    # force=True: always call T104, skip the step-0 validity probe
    get_symmetric_key(company, force=True)

    # ── Reload the AES key fresh from DB ──────────────────────────────────────
    # T104 saved the new key via its own client instance (different object).
    # client.settings and client._km are now stale — refresh them.
    client._aes_key = None
    client._km = None  # force KeyManager rebuild with reloaded settings

    try:
        # Reload the settings document from DB to get the key T104 just wrote
        fresh = frappe.get_doc("EFRIS Settings", client.settings.name)
        client.settings = fresh
    except Exception:
        pass  # if reload fails, key_manager may still find it via db_get_value

    try:
        key = client.key_manager.get_aes_key()
        client._aes_key = key
        set_global_aes_key(company, key)
        frappe.logger("efris").info(
            f"[{company}] session refreshed via T104 — "
            f"new AES key loaded ({len(key) * 8}-bit)"
        )
    except Exception as exc:
        frappe.logger("efris").error(
            f"[{company}] refresh_session: could not load new key after T104: {exc}"
        )
        raise


def handle_session_error(
    t_code: str,
    company: str,
    exc: "EFRISAPIError",
    doc_type: str = "",
    doc_name: str = "",
) -> None:
    """
    Called when a T-code returns a SESSION_ERRORS code.

    1. Clears the global AES key cache so the next request reloads from DB.
    2. Logs the failure to EFRIS Log.
    3. Throws a short, clear error telling the user to update the key in
       EFRIS Settings — exactly as SAP B1 would display an error if
       SessionAESKey.txt is wrong.

    Does NOT attempt T104 auto-refresh mid-operation — that risks overwriting
    a valid key with a wrong one when the private key doesn't match.
    """
    from efris_integration.utils.helpers import log_efris

    # Invalidate cache so the next call reloads fresh from DB
    clear_global_aes_key(company)

    # Log the failure
    log_efris(
        doc_type, doc_name, t_code,
        {}, {}, "Failed",
        f"Session error [{exc.code}]: {exc.message}",
    )

    frappe.throw(
        f"EFRIS {t_code} failed [<b>{exc.code}</b>]: {exc.message}<br><br>"
        f"The <b>Session AES Key</b> in <b>EFRIS Settings</b> for "
        f"<b>{company}</b> may be stale.<br>"
        f"Go to <b>EFRIS Settings → Session AES Key</b>, update it and try again.",
        title=f"EFRIS {t_code} — Session Error",
    )


def throw_session_key_error(t_code: str, company: str, orig_code: str, orig_msg: str) -> None:
    """Raise a short, clear session error (used by modules that catch EFRISAPIError directly)."""
    clear_global_aes_key(company)
    frappe.throw(
        f"EFRIS {t_code} failed [<b>{orig_code}</b>]: {orig_msg}<br><br>"
        f"Go to <b>EFRIS Settings → Session AES Key</b> for <b>{company}</b>, "
        f"update the key and try again.",
        title=f"EFRIS {t_code} — Session Error",
    )
