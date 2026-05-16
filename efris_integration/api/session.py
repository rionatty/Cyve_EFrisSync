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
    Raise an actionable frappe error when a session error persists after refresh.

    Gives the user the exact 5-step procedure to paste the key from SAP B1.

    Args:
        t_code:    EFRIS interface code, e.g. "T130" (shown in the error title).
        company:   Company name (shown in the error body).
        orig_code: The EFRIS returnCode that triggered this (e.g. "15").
        orig_msg:  The EFRIS returnMessage.
    """
    frappe.throw(
        f"<b>EFRIS {t_code} — Session AES Key mismatch "
        f"[error {orig_code}]</b><br><br>"
        f"EFRIS returned: <i>{orig_msg}</i><br><br>"
        "EFRIS cannot decrypt the request because the <b>Session AES Key</b> in "
        f"EFRIS Settings for <b>{company}</b> does not match the active key on "
        "the EFRIS server.<br><br>"
        "<b>How to fix (5 steps):</b><br>"
        "1. On the SAP B1 server open <b>SessionAESKey.txt</b>.<br>"
        "2. Copy the base64 key value (e.g. <code>x/SVvBh4S49ddPNX+nXQYg==</code>).<br>"
        "3. In ERPNext open <b>EFRIS Settings → Session AES Key</b>, "
        "paste the value and <b>Save</b>.<br>"
        "4. <b>Do NOT</b> click Full Initialise or T103 Sign-In — "
        "that resets the EFRIS session and invalidates the key you just pasted.<br>"
        "5. Try the operation again.",
        title="Session AES Key Mismatch",
    )
