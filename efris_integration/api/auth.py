"""
EFRIS Authentication  —  T101 → T102 → T103 → T104

Mirrors: ClsSBOAddOn.InitializeApp(), T101.cs, T102.cs, T103.cs, T104.cs

Key lifecycle:
  T102 returns server_public_key (plain) + client_private_key (AES-encrypted).
       AES key = TaxpayerID[:10] + date("ddMMyy")
       Decrypted private key is saved to PrivateKey.txt (or DB)

  T104 returns the session AES key, RSA-encrypted with the server public key.
       We decrypt with the client private key and save to SessionAESKey.txt (or DB)

All subsequent calls use the session AES key for payload encryption.
"""

import base64
from datetime import datetime, timedelta

import frappe

from efris_integration.api.base import EFRISClient
from efris_integration.utils.encryption import (
    aes_decrypt,
    derive_private_key_aes_key,
    rsa_decrypt,
)
from efris_integration.utils.helpers import now_str


# ─── T101: Get Server Time ─────────────────────────────────────────────────

@frappe.whitelist()
def get_server_time() -> str:
    """T101 — Test connectivity and retrieve EFRIS server timestamp."""
    client = EFRISClient()
    try:
        result = client.call("T101", encrypt=False)
    except Exception as exc:
        frappe.throw(f"T101 failed: {exc}")
    server_time = result.get("currentTime", "")
    frappe.msgprint(
        f"✅ EFRIS connection OK — Server time: {server_time}",
        indicator="green",
        alert=True,
    )
    return server_time


# ─── T102: Client Initialisation ──────────────────────────────────────────

@frappe.whitelist()
def client_init() -> dict:
    """
    T102 — Download server RSA public key + obtain AES-encrypted client private key.

    Decryption key for the private key:
        AES-128-ECB, key = (TaxpayerID[:10] + date_as_ddMMyy).encode()[:16]
    """
    client = EFRISClient()
    settings = client.settings

    result = client.call("T102", encrypt=False)

    # EFRIS uses abbreviated field names: serverPubKey / clientPriKey
    server_pub_key     = (result.get("serverPubKey")
                          or result.get("serverPublicKey", ""))
    encrypted_priv_key = (result.get("clientPriKey")
                          or result.get("clientPrivateKey", ""))

    if not server_pub_key or not encrypted_priv_key:
        # Log the full result so we can see what field names EFRIS actually used
        frappe.log_error(
            f"T102 raw result keys: {list(result.keys())}\nFull result: {result}",
            "EFRIS T102 Unexpected Response",
        )
        frappe.throw(
            "T102 returned incomplete key data.<br>"
            f"serverPublicKey: {'✅' if server_pub_key else '❌ missing'}<br>"
            f"clientPrivateKey: {'✅' if encrypted_priv_key else '❌ missing'}<br><br>"
            f"Response keys found: <code>{list(result.keys())}</code><br>"
            "Check <b>Error Log</b> for the full T102 response to see the actual field names."
        )

    # Decrypt private key
    today_ddmmyy = datetime.now().strftime("%d%m%y")
    aes_key = derive_private_key_aes_key(settings.taxpayer_id, today_ddmmyy)
    priv_key_bytes = aes_decrypt(base64.b64decode(encrypted_priv_key), aes_key)
    private_key_pem = priv_key_bytes.decode("utf-8").strip()

    # Persist via KeyManager (writes to file if path configured, always writes to DB)
    km = client.key_manager
    km.save_public_key(server_pub_key)
    km.save_private_key(private_key_pem)

    settings.db_set("init_status", "T102 OK — Keys obtained", update_modified=False)

    frappe.msgprint("✅ T102: Server public key and client private key obtained.", indicator="green")
    return {"status": "OK", "server_pub_key_preview": server_pub_key[:40] + "..."}


# ─── T103: Sign In ────────────────────────────────────────────────────────

@frappe.whitelist()
def sign_in() -> dict:
    """T103 — Authenticate with EFRIS username/password."""
    client = EFRISClient()
    settings = client.settings

    password = settings.get_password("efris_password", raise_exception=False) or ""
    if not password:
        frappe.throw("EFRIS password not configured in EFRIS Settings.")

    payload = {
        "userName": settings.efris_username,
        "password": password,
        "tin": settings.tin,
        "appId": settings.app_id,
    }

    result = client.call("T103", payload, encrypt=False)

    token = result.get("token", "")
    expiry = (datetime.now() + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")

    if token:
        settings.db_set("auth_token", token, update_modified=False)
        settings.db_set("token_expiry", expiry, update_modified=False)
        settings.db_set("init_status", "T103 OK — Authenticated", update_modified=False)

    frappe.msgprint("✅ T103: Sign-in successful.", indicator="green")
    return {"status": "Authenticated", "expiry": expiry}


# ─── T104: Get Symmetric AES Key ──────────────────────────────────────────

@frappe.whitelist()
def get_symmetric_key() -> dict:
    """
    T104 — Retrieve session AES key.

    EFRIS returns the AES key RSA-encrypted with the server public key.
    We decrypt it using the client private key, then store it.
    """
    client = EFRISClient()
    settings = client.settings

    private_key_pem = client.key_manager.get_private_key()
    if not private_key_pem:
        frappe.throw("Client private key not found. Run T102 first.")

    result = client.call("T104", encrypt=False)

    # Field name varies by EFRIS version: passwordDes or symmKey
    encrypted_aes_b64 = (result.get("passwordDes")
                         or result.get("symmKey")
                         or result.get("symmetricKey", ""))
    if not encrypted_aes_b64:
        frappe.log_error(
            f"T104 raw result keys: {list(result.keys())}\nFull: {result}",
            "EFRIS T104 Unexpected Response",
        )
        frappe.throw(
            f"T104 returned no AES key.<br>"
            f"Response keys found: <code>{list(result.keys())}</code><br>"
            "Check <b>Error Log</b> for the full T104 response."
        )

    aes_key_bytes = rsa_decrypt(encrypted_aes_b64, private_key_pem)
    client.set_aes_key(aes_key_bytes)

    settings.db_set("init_status", "T104 OK — Session key active", update_modified=False)

    frappe.msgprint(
        f"✅ T104: Session AES key obtained ({len(aes_key_bytes) * 8}-bit).",
        indicator="green",
    )
    return {"status": "AES Key Set", "key_bits": len(aes_key_bytes) * 8}


# ─── Full Init Sequence ───────────────────────────────────────────────────

@frappe.whitelist()
def full_initialise() -> dict:
    """
    Run the complete T101 → T102 → T103 → T104 sequence in one click.
    Matches the SAP B1 'Test Connection' + initialise flow.
    """
    results = {}

    frappe.publish_progress(10, title="EFRIS Init", description="T101 — Testing connection…")
    results["server_time"] = get_server_time()

    frappe.publish_progress(30, title="EFRIS Init", description="T102 — Getting client keys…")
    results["t102"] = client_init()

    frappe.publish_progress(60, title="EFRIS Init", description="T103 — Signing in…")
    results["t103"] = sign_in()

    frappe.publish_progress(85, title="EFRIS Init", description="T104 — Getting session key…")
    results["t104"] = get_symmetric_key()

    frappe.publish_progress(100, title="EFRIS Init", description="Done")

    frappe.msgprint(
        f"🎉 EFRIS fully initialised!\n"
        f"Server time: {results['server_time']}\n"
        f"All keys stored. Ready to submit documents.",
        indicator="green",
        title="EFRIS Initialised",
    )
    return results


# ─── Scheduler: refresh session before expiry ─────────────────────────────

def refresh_session_if_needed():
    """Called hourly by the scheduler. Re-authenticates if token near expiry."""
    try:
        settings = frappe.get_single("EFRIS Settings")
        if not settings.enabled:
            return

        expiry_str = settings.get("token_expiry")
        if not expiry_str:
            return

        if isinstance(expiry_str, str):
            expiry = datetime.strptime(expiry_str, "%Y-%m-%d %H:%M:%S")
        else:
            expiry = expiry_str

        if datetime.now() >= expiry - timedelta(minutes=30):
            frappe.logger("efris").info("EFRIS: refreshing session (token expiring soon).")
            sign_in()
            get_symmetric_key()

    except Exception:
        frappe.log_error(frappe.get_traceback(), "EFRIS Session Refresh Failed")
