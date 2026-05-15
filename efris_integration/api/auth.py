"""
EFRIS Authentication  —  T101 → T102 → T103 → T104

Mirrors exactly: ClsSBOAddOn.cs, URASetting.cs, T102.cs, T103.cs, T104.cs

Key lifecycle (from SAP source):
  T101 → Test connection, returns T101.CurrentTime
  T102 → Returns T102.clientPriKey (AES-encrypted) + T102.serverPubKey
           AES key = TaxpayerID[:10] + date("ddMMyy"), padded/truncated to 16 bytes
           Decrypted private key written to PrivateKey.txt  (U_OT_PK path)
           Server public key written to PublicKey.txt        (U_OT_EPK path)
  T103 → Returns T103: Device, Taxpayer, TaxpayerBranch, TaxTypes (no password sent)
           TIN + TaxpayerID auto-updated from response
  T104 → Returns T104.passowrdDes (note: SAP source typo — double 'o')
           RSA-decrypted with client private key → session AES key
           Written to SessionAESKey.txt  (U_OT_SAK path)

No username/password is stored or sent. Authentication is entirely via
the RSA key-exchange in T102/T104.
"""

from datetime import datetime

import frappe

from efris_integration.api.base import EFRISClient
from efris_integration.utils.encryption import (
    rsa_decrypt,
    try_decrypt_private_key,
)


# ─── T101: Test Connection ─────────────────────────────────────────────────

@frappe.whitelist()
def get_server_time() -> str:
    """T101 — Mirrors TestEFRISConnection() in URASetting.cs."""
    client = EFRISClient()
    try:
        result = client.call("T101", encrypt=False)
    except Exception as exc:
        frappe.throw(f"T101 failed: {exc}")

    # T101.cs: JsonPropertyName("currentTime")
    server_time = result.get("currentTime", "")
    frappe.msgprint(
        f"✅ EFRIS connection OK — Server time: {server_time}",
        indicator="green",
        alert=True,
    )
    return server_time


# ─── T102: Get Client Keys ────────────────────────────────────────────────

@frappe.whitelist()
def client_init() -> dict:
    """
    T102 — Download server RSA public key + AES-encrypted client private key.

    T102.cs fields (from SAP source):
        clientPriKey  — client private key, AES-128-ECB encrypted
        serverPubKey  — server RSA public key (plain PEM)
        keyTable      — (ignored)

    AES key for private key decryption:
        try_decrypt_private_key() tries every known EFRIS key derivation strategy.
        Standard (SAP B1 EncryptionHelper.cs): TaxpayerID[:10] + ddMMyy → 16 bytes
    """
    client   = EFRISClient()
    settings = client.settings

    result = client.call("T102", encrypt=False)

    # Exact field names from T102.cs
    server_pub_key     = result.get("serverPubKey", "")
    encrypted_priv_key = result.get("clientPriKey", "")

    if not server_pub_key or not encrypted_priv_key:
        frappe.log_error(
            f"T102 response keys: {list(result.keys())}\nFull response: {result}",
            "EFRIS T102 Unexpected Response",
        )
        frappe.throw(
            f"T102 returned incomplete key data.<br>"
            f"serverPubKey: {'✅' if server_pub_key else '❌'}<br>"
            f"clientPriKey: {'✅' if encrypted_priv_key else '❌'}<br>"
            f"Keys in response: <code>{list(result.keys())}</code>"
        )

    # Log the raw values for diagnostics (first 80 chars only — safe to log)
    frappe.logger("efris").debug(
        f"T102 clientPriKey[:80]={encrypted_priv_key[:80]}  "
        f"serverPubKey[:40]={server_pub_key[:40]}"
    )

    # Decrypt private key — tries every known key derivation strategy automatically
    try:
        private_key_pem = try_decrypt_private_key(
            encrypted_priv_key,
            taxpayer_id=settings.taxpayer_id or "",
            tin=settings.tin or "",
        )
    except ValueError as exc:
        frappe.throw(str(exc), title="T102 Private Key Decrypt Failed")

    # Write to files (U_OT_EPK path, U_OT_PK path)
    km = client.key_manager
    km.save_public_key(server_pub_key)
    km.save_private_key(private_key_pem)

    frappe.msgprint("✅ T102: Server public key and client private key saved to files.", indicator="green")
    return {"status": "OK", "server_pub_key_preview": server_pub_key[:40] + "…"}


# ─── T103: Sign In / Get Device Info ──────────────────────────────────────

@frappe.whitelist()
def sign_in() -> dict:
    """
    T103 — Mirrors T103.cs response model.

    SAP sends empty payload; EFRIS returns:
        T103.Device, T103.Taxpayer, T103.TaxpayerBranch, T103.TaxTypes, …

    NO username/password is sent. Authentication is via the RSA key exchange.
    TIN and TaxpayerID in settings are updated from the response if present.
    """
    client   = EFRISClient()
    settings = client.settings

    result = client.call("T103", encrypt=False)

    # Extract taxpayer info from T103 response and persist to settings
    taxpayer = result.get("taxpayer", {})
    if isinstance(taxpayer, dict):
        tin = taxpayer.get("tin", "") or taxpayer.get("taxpayerTin", "")
        tp_id = taxpayer.get("taxpayerId", "") or taxpayer.get("taxPayerID", "")
        if tin:
            settings.db_set("tin", tin, update_modified=False)
        if tp_id:
            settings.db_set("taxpayer_id", tp_id, update_modified=False)

    frappe.msgprint("✅ T103: Device / taxpayer info received from EFRIS.", indicator="green")
    return {"status": "OK", "taxpayer": taxpayer}


# ─── T104: Get Session AES Key ────────────────────────────────────────────

@frappe.whitelist()
def get_symmetric_key() -> dict:
    """
    T104 — Mirrors T104.cs response model.

    T104.cs fields (from SAP source, note the typo in the original):
        passowrdDes  — session AES key RSA-encrypted with server public key

    We decrypt with the client private key and write to SessionAESKey.txt.
    """
    client   = EFRISClient()
    settings = client.settings

    private_key_pem = client.key_manager.get_private_key()
    if not private_key_pem:
        frappe.throw("Client private key not found. Run T102 first.")

    result = client.call("T104", encrypt=False)

    # T104.cs: JsonPropertyName("passowrdDes") — SAP typo, two 'o's
    encrypted_aes_b64 = result.get("passowrdDes", "") or result.get("passwordDes", "")
    if not encrypted_aes_b64:
        frappe.log_error(
            f"T104 keys: {list(result.keys())}\nFull: {result}",
            "EFRIS T104 Unexpected Response",
        )
        frappe.throw(
            f"T104 returned no AES key.<br>"
            f"Response keys: <code>{list(result.keys())}</code><br>"
            "Check <b>Error Log</b> for full T104 response."
        )

    aes_key_bytes = rsa_decrypt(encrypted_aes_b64, private_key_pem)
    client.set_aes_key(aes_key_bytes)   # writes to SessionAESKey.txt

    frappe.msgprint(
        f"✅ T104: Session AES key ({len(aes_key_bytes) * 8}-bit) saved to file.",
        indicator="green",
    )
    return {"status": "AES Key Set", "key_bits": len(aes_key_bytes) * 8}


# ─── Full Init Sequence ───────────────────────────────────────────────────

@frappe.whitelist()
def full_initialise() -> dict:
    """
    T101 → T102 → T103 → T104 in one call.
    Mirrors the SAP B1 'Test Connection' + initialise flow.
    """
    results = {}

    frappe.publish_progress(10,  title="EFRIS Init", description="T101 — Testing connection…")
    results["server_time"] = get_server_time()

    frappe.publish_progress(35,  title="EFRIS Init", description="T102 — Getting client keys…")
    results["t102"] = client_init()

    frappe.publish_progress(65,  title="EFRIS Init", description="T103 — Getting device info…")
    results["t103"] = sign_in()

    frappe.publish_progress(90,  title="EFRIS Init", description="T104 — Getting session key…")
    results["t104"] = get_symmetric_key()

    frappe.publish_progress(100, title="EFRIS Init", description="Done ✅")

    frappe.msgprint(
        f"🎉 EFRIS initialised!\n"
        f"Server time: {results['server_time']}\n"
        f"All keys saved to files. Ready to submit documents.",
        indicator="green",
        title="EFRIS Initialised",
    )
    return results


# ─── Scheduler: refresh session if needed ────────────────────────────────

def refresh_session_if_needed():
    """Called hourly. Re-runs T103 + T104 if files are stale (>8 h old)."""
    import os
    import time
    try:
        settings = frappe.get_single("EFRIS Settings")
        if not settings.api_url:
            return

        path = settings.get("session_key_path")
        if not path or not os.path.isfile(path):
            return

        age_hours = (time.time() - os.path.getmtime(path)) / 3600
        if age_hours > 7.5:
            frappe.logger("efris").info("EFRIS: refreshing session (key >7.5h old).")
            sign_in()
            get_symmetric_key()

    except Exception:
        frappe.log_error(frappe.get_traceback(), "EFRIS Session Refresh Failed")
