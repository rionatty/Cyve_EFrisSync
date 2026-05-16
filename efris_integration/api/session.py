"""
Shared EFRIS session management — used by all T-code modules.

Every module that calls an encrypted/signed T-code (T126, T130, T131, …)
should use ensure_session() before the first call and handle session errors
with SESSION_ERRORS + refresh_session() + retry.

Design rules (same as SAP B1 pattern):
  • Load the existing AES key from DB — never call T103/T104 proactively.
  • T103 (sign-in) RESETS the EFRIS session AES key on the server. Calling it
    when T104 cannot follow up (wrong private key) invalidates the SAP B1 key
    too. T103 is therefore NEVER called from session refresh — only T104.
  • T104 only: retrieves the current active key without disturbing SAP B1.
  • On first run (no key stored): T104 to initialise, still no T103.

Shared constants
────────────────
SESSION_ERRORS   — EFRIS error codes that mean "wrong / stale AES key".
                   Import this in every module instead of defining a local set.

WRONG_KEY_CODES  — Superset used by auth.py T104 candidate probing.
                   Includes additional SAP B1 environment variant codes.
"""

import frappe

from efris_integration.api.base import EFRISClient

# ─── Shared error code sets ───────────────────────────────────────────────────

# Codes returned by EFRIS when it cannot decrypt our AES-encrypted payload:
#   15   = Data decryption error   (PKCS7 unpad failed — definitely wrong key)
#   2066 = Illegal json format     (PKCS7 passed by chance, decrypted to garbage)
#   16   = Signature verify error  (session/key mismatch)
#   99   = General session error
#   SESSION_EXPIRED = we couldn't decrypt EFRIS's response (wrong key on our side)
SESSION_ERRORS: frozenset[str] = frozenset({
    "15", "2066", "16", "99", "SESSION_EXPIRED",
})

# Superset — auth.py T104 key-candidate probing also checks these additional codes
# found in some EFRIS test/production environment variants.
WRONG_KEY_CODES: frozenset[str] = SESSION_ERRORS | frozenset({"9999", "9998"})


# ─── Session helpers ──────────────────────────────────────────────────────────

def ensure_session(client: EFRISClient, company: str) -> None:
    """
    Load the existing session AES key from DB before the first T-code call.

    Mirrors SAP B1: read SessionAESKey.txt directly — no T103, no T104
    proactively. Only falls back to T104 if no key is stored at all (first run).

    Args:
        client:  An EFRISClient already instantiated for the company.
        company: Company name (used for log messages only).
    """
    try:
        client._aes_key = client.key_manager.get_aes_key()
        frappe.logger("efris").info(
            f"[{company}] session key loaded: {len(client._aes_key) * 8}-bit"
        )
        return
    except RuntimeError:
        pass  # no key stored → need to initialise

    # No key stored — try T104 only (first-run).
    frappe.logger("efris").info(
        f"[{company}] no session key stored — running T104 to initialise"
    )
    refresh_session(client, company)
    client._aes_key = None
    client._aes_key = client.key_manager.get_aes_key()


def refresh_session(client: EFRISClient, company: str) -> None:
    """
    Refresh the session AES key via T104 (forced, bypassing step-0 probe).

    Called when a T-code returns a SESSION_ERRORS code indicating the stored
    AES key no longer matches what EFRIS expects.

    CRITICAL — T103 is NOT called here:
      T103 resets the EFRIS session AES key on the server. If T104 then fails
      (private key mismatch), no valid key remains — and SAP B1's file is also
      now invalid. T104 alone retrieves the active key without disturbing the
      SAP B1 session.

    Args:
        client:  EFRISClient for the company (its _aes_key will be cleared).
        company: Company name.

    Raises:
        Any exception from get_symmetric_key (e.g. if private key is wrong).
        Callers should convert this to a user-facing frappe.throw with the
        instruction to paste SessionAESKey.txt into EFRIS Settings manually.
    """
    from efris_integration.api.auth import get_symmetric_key

    # force=True: always call T104, skip the step-0 validity probe
    get_symmetric_key(company, force=True)
    client._aes_key = None  # force reload from DB on next _get_aes_key() call


def throw_session_key_error(t_code: str, company: str, orig_code: str, orig_msg: str) -> None:
    """
    Raise a short, clear error when a session error persists after T104 refresh.
    """
    frappe.throw(
        f"EFRIS {t_code} failed [<b>{orig_code}</b>]: {orig_msg}<br><br>"
        f"Please update the <b>Session AES Key</b> in "
        f"<b>EFRIS Settings</b> for <b>{company}</b> and try again.",
        title=f"EFRIS {t_code} Error",
    )
