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

from efris_integration.api.base import EFRISClient, EFRISAPIError
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

    URA documentation (T102 flow):
        1. Client sends T102 with optional OTP field.
        2. EFRIS encrypts the private key:
             - With keyTable returned: decrypt using keyTable as AES key (white-box)
             - Without keyTable: decrypt using the OTP sent in the request
        3. Returns: clientPriKey, serverPubKey, keyTable (may be absent)
        4. Client decrypts clientPriKey and saves both key files.

    If decryption fails but the private key file already exists and is valid
    (i.e. T104 works), we log a warning and keep the existing file rather
    than failing — the key is already correctly set up.
    """
    import random

    client   = EFRISClient()
    settings = client.settings

    result = client.call("T102", encrypt=False)

    # Exact field names from T102 docs
    server_pub_key     = result.get("serverPubKey", "")
    encrypted_priv_key = result.get("clientPriKey", "")
    key_table          = result.get("keyTable", "")

    if not server_pub_key or not encrypted_priv_key:
        frappe.log_error(
            f"T102 response keys: {list(result.keys())}\nFull response: {result}",
            "EFRIS T102 Unexpected Response",
        )
        frappe.throw(
            f"T102 returned incomplete key data.<br>"
            f"serverPubKey: {'✅' if server_pub_key else '❌'}<br>"
            f"clientPriKey: {'✅' if encrypted_priv_key else '❌'}<br>"
            f"keyTable    : {'✅' if key_table else '❌ (missing)'}<br>"
            f"Keys in response: <code>{list(result.keys())}</code>"
        )

    frappe.logger("efris").info(
        f"T102  keyTable={'present' if key_table else 'MISSING'}\n"
        f"T102  clientPriKey[:80] = {encrypted_priv_key[:80]}\n"
        f"T102  serverPubKey[:60] = {server_pub_key[:60]}"
    )

    km = client.key_manager

    # Always save the server public key (unencrypted — just store it)
    km.save_public_key(server_pub_key)

    # Attempt to decrypt clientPriKey with all known strategies
    private_key_pem = None
    decrypt_error   = None
    try:
        private_key_pem = try_decrypt_private_key(
            encrypted_priv_key,
            taxpayer_id=settings.taxpayer_id or "",
            tin=settings.tin or "",
            key_table=key_table,
            device_no=settings.device_no or "",
        )
    except ValueError as exc:
        decrypt_error = str(exc)

    if private_key_pem:
        km.save_private_key(private_key_pem)
        frappe.msgprint(
            "✅ T102: Server public key and client private key saved to files.",
            indicator="green",
        )
        return {"status": "OK", "server_pub_key_preview": server_pub_key[:40] + "…"}

    # ── Decryption failed — check if existing private key is still valid ──
    existing_key_ok = False
    try:
        existing_pem = km.get_private_key()
        from efris_integration.utils.encryption import _import_rsa_key
        _import_rsa_key(existing_pem)   # raises if invalid
        existing_key_ok = True
    except Exception:
        pass

    if existing_key_ok:
        # Keep the existing working key — just update the server public key
        frappe.log_error(
            f"T102 clientPriKey decryption failed (keyTable missing, OTP={otp}).\n"
            f"Existing private key file is valid — kept as-is.\n"
            f"Error: {decrypt_error}\n"
            f"clientPriKey[:120]: {encrypted_priv_key[:120]}",
            "EFRIS T102 Decrypt Warning",
        )
        frappe.msgprint(
            "⚠️ T102: Server public key updated.<br><br>"
            "The EFRIS server did not return a decryptable private key "
            "(<code>keyTable</code> missing).<br>"
            "Your existing private key file is still valid — no change made to it.<br><br>"
            "If you need to re-download the private key, contact URA support for "
            "the correct decryption method for your EFRIS environment.",
            indicator="orange",
            title="T102 Partial Success",
        )
        return {"status": "partial", "server_pub_key_updated": True, "private_key_kept": True}

    # No existing key and decryption failed — hard error
    frappe.throw(decrypt_error or "T102 clientPriKey decryption failed.", title="T102 Private Key Decrypt Failed")


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
    T104 — Get the session AES key from EFRIS and verify it works.

    Strategy:
      1. Call T104 → receive passowrdDes (RSA-encrypted AES key)
      2. Build every plausible candidate AES key from passowrdDes
         (PKCS1v1.5, OAEP, raw slices, hashes)
      3. Immediately probe T126 with each candidate until one decrypts
         the T126 response without error — that is the correct AES key
      4. Save the verified key to SessionAESKey.txt and return

    This approach completely bypasses the "which RSA padding does EFRIS use?"
    question by letting T126's AES decryption be the arbiter of correctness.
    """
    import base64 as _b64
    import hashlib

    client = EFRISClient()

    try:
        private_key_pem = client.key_manager.get_private_key()
    except Exception as e:
        frappe.log_error(
            f"T104: private key read error — {e}  path={client.settings.get('private_key_path')}",
            "EFRIS T104 Private Key Read Error",
        )
        private_key_pem = ""

    t104_result = client.call("T104", encrypt=False)

    encrypted_aes_b64 = (
        t104_result.get("passowrdDes", "")
        or t104_result.get("passwordDes", "")
    ).strip()

    if not encrypted_aes_b64:
        frappe.log_error(
            f"T104 keys: {list(t104_result.keys())}\nFull: {t104_result}",
            "EFRIS T104 Unexpected Response",
        )
        frappe.throw(
            f"T104 returned no passowrdDes field.<br>"
            f"Response keys: <code>{list(t104_result.keys())}</code>"
        )

    raw = _b64.b64decode(encrypted_aes_b64.rstrip("=") + "==")
    frappe.logger("efris").info(
        f"T104 passowrdDes: {len(raw)} bytes  first16={raw[:16].hex()}"
    )

    # ── Build every plausible candidate AES key ────────────────────────────
    candidates = _build_aes_key_candidates(raw, private_key_pem)
    frappe.logger("efris").info(
        f"T104: {len(candidates)} AES key candidates to probe: "
        + ", ".join(f"[{l}]={k.hex()[:16]}…" for l, k in candidates)
    )

    # ── Probe T126 with each candidate — verified key decrypts without error ─
    network_error_label = None
    for label, key_bytes in candidates:
        client._aes_key = key_bytes  # inject candidate into this client instance

        try:
            # T126 probe: empty payload, response is AES-encrypted with the session key.
            # If AES decrypt succeeds → this is the correct key.
            client.call(
                "T126",
                payload=None,
                encrypt=False,
                doc_type="__t104_probe__",
                doc_name=label,
            )
            # ✅ Got here → T126 decrypted successfully → key is correct
            client.set_aes_key(key_bytes)       # persist to SessionAESKey.txt
            client._aes_key = key_bytes          # keep in cache
            frappe.logger("efris").info(
                f"T104 ✅ AES key confirmed by T126 probe [{label}]: "
                f"{len(key_bytes)*8}-bit  {key_bytes.hex()}"
            )
            frappe.msgprint(
                f"✅ T104: Session AES key ({len(key_bytes)*8}-bit) saved.",
                indicator="green",
            )
            return {"status": "AES Key Set", "key_bits": len(key_bytes) * 8, "method": label}

        except EFRISAPIError as exc:
            if exc.code == "SESSION_EXPIRED":
                # AES decrypt failed for this candidate → try next
                frappe.logger("efris").warning(
                    f"T104 candidate [{label}] SESSION_EXPIRED (wrong key)"
                )
                client._aes_key = None
                continue
            # Non-SESSION_EXPIRED EFRIS error → AES decrypt DID succeed (got past it),
            # EFRIS just returned a non-00 code for some other reason.
            # The key is correct; save it and return.
            client.set_aes_key(key_bytes)
            client._aes_key = key_bytes
            frappe.logger("efris").info(
                f"T104 ✅ AES key confirmed [{label}] (T126 returned [{exc.code}] "
                f"but decryption succeeded): {key_bytes.hex()}"
            )
            frappe.msgprint(
                f"✅ T104: Session AES key ({len(key_bytes)*8}-bit) saved.",
                indicator="green",
            )
            return {"status": "AES Key Set", "key_bits": len(key_bytes) * 8, "method": label}

        except Exception as exc:
            # Network / HTTP error — can't tell if key is right
            frappe.logger("efris").warning(
                f"T104 candidate [{label}] network error: {exc}"
            )
            if network_error_label is None:
                network_error_label = label
            client._aes_key = None
            continue

    # ── All candidates failed ──────────────────────────────────────────────
    if network_error_label and candidates:
        # Network errors prevented probing — fall back to PKCS1v1.5 result
        # (best cryptographic guess) so the system can still try to operate.
        _, fallback_key = candidates[0]
        client.set_aes_key(fallback_key)
        frappe.log_error(
            f"T104: all T126 probes hit network errors. Using first candidate as fallback.\n"
            f"fallback key: {fallback_key.hex()}",
            "EFRIS T104 Probe Network Error",
        )
        frappe.msgprint(
            f"⚠️ T104: AES key saved (unverified — T126 network errors during probe).",
            indicator="orange",
        )
        return {"status": "AES Key Set (unverified)", "key_bits": len(fallback_key) * 8}

    # Truly all failed — private key definitely wrong
    candidates_log = "\n".join(f"  [{l}] {k.hex()}" for l, k in candidates)
    frappe.log_error(
        f"T104: all {len(candidates)} AES key candidates failed T126 SESSION_EXPIRED.\n"
        f"passowrdDes raw[:32]: {raw[:32].hex()}\n"
        f"private_key loaded: {'yes' if private_key_pem else 'NO'}\n"
        f"Candidates tried:\n{candidates_log}",
        "EFRIS T104 All Candidates Failed",
    )
    frappe.db.commit()

    frappe.throw(
        "T104: None of the candidate AES keys could decrypt EFRIS responses.<br><br>"
        "This means the <b>private key in PrivateKey.txt does not match</b> "
        "the public key registered with EFRIS.<br><br>"
        "To fix:<br>"
        "1. Run <b>T102 (Client Init)</b> to re-download the correct private key<br>"
        "2. Or generate a new key pair and re-register the public key with EFRIS<br><br>"
        "See <b>Error Log → EFRIS T104 All Candidates Failed</b> for raw bytes.",
        title="T104 AES Key Verification Failed",
    )


def _build_aes_key_candidates(raw_passowrdDes: bytes, private_key_pem: str) -> list:
    """
    Build every plausible AES session key from T104 passowrdDes.
    Ordered from most-likely (RSA decrypt) to least-likely (raw slices).
    All entries have key length in (16, 24, 32) bytes.
    """
    import hashlib
    from Crypto.Cipher import PKCS1_v1_5, PKCS1_OAEP as _OAEP
    from efris_integration.utils.encryption import _import_rsa_key

    seen: set[bytes] = set()
    out: list[tuple[str, bytes]] = []

    def _add(label: str, key: bytes):
        if len(key) in (16, 24, 32) and key not in seen:
            seen.add(key)
            out.append((label, key))

    # 1. RSA PKCS1v1.5 (official URA — most likely to be correct)
    if private_key_pem:
        try:
            rsa_key  = _import_rsa_key(private_key_pem)
            sentinel = object()
            v15      = PKCS1_v1_5.new(rsa_key).decrypt(raw_passowrdDes, sentinel)
            if v15 is not sentinel:
                frappe.logger("efris").info(
                    f"PKCS1v1.5 raw result: {len(v15)} bytes  hex={v15.hex()}"
                )
                _add("pkcs1v15-full", v15)
                if len(v15) >= 16:
                    _add("pkcs1v15-first16", v15[:16])
                if len(v15) >= 16:
                    _add("pkcs1v15-last16",  v15[-16:])
            else:
                frappe.logger("efris").warning("PKCS1v1.5 returned sentinel")
        except Exception as exc:
            frappe.logger("efris").warning(f"PKCS1v1.5 exception: {exc}")

    # 2. RSA OAEP
    if private_key_pem:
        try:
            rsa_key = _import_rsa_key(private_key_pem)
            oaep    = _OAEP.new(rsa_key).decrypt(raw_passowrdDes)
            frappe.logger("efris").info(
                f"OAEP raw result: {len(oaep)} bytes  hex={oaep.hex()}"
            )
            _add("oaep-full", oaep)
            if len(oaep) >= 16:
                _add("oaep-first16", oaep[:16])
        except Exception as exc:
            frappe.logger("efris").warning(f"OAEP exception: {exc}")

    # 3. Raw slices of passowrdDes (if EFRIS sends AES key unencrypted or
    #    uses a non-RSA encoding in some environments)
    r = raw_passowrdDes
    _add("raw-first16", r[:16])
    _add("raw-first24", r[:24])
    _add("raw-first32", r[:32])
    _add("raw-last16",  r[-16:])

    # 4. Cryptographic digests of passowrdDes (some EFRIS variants)
    _add("md5",            hashlib.md5(r).digest())
    _add("sha256-first16", hashlib.sha256(r).digest()[:16])
    _add("sha256-last16",  hashlib.sha256(r).digest()[-16:])
    _add("sha256-full",    hashlib.sha256(r).digest())

    return out


# ─── T104 AES key extraction ─────────────────────────────────────────────

def _extract_aes_key(value: str, private_key_pem: str) -> bytes:
    """
    Extract the session AES key from T104 passowrdDes.

    Official URA guide (page 6):
      "The value in passowrdDes is RSA encrypted using your public key,
       so you have to decrypt it using your private key."

    The private key is the one YOU generated and registered with EFRIS.
    Key size: 2048 bit, algorithm: SHA1withRSA (PKCS1v1.5 padding).

    Order of attempts:
      1. RSA-PKCS1v1.5   ← official URA (SHA1withRSA signing = PKCS1v1.5)
      2. RSA-OAEP        ← fallback
      3. Plain base64    ← test/dev environments that skip RSA
      4. Hex string      ← rare dev environments
    """
    import base64 as _b64
    from efris_integration.utils.encryption import _import_rsa_key
    from Crypto.Cipher import PKCS1_v1_5, PKCS1_OAEP as _OAEP

    # Strip any extra whitespace and re-pad; passowrdDes may already have '=='
    value_clean = value.strip().rstrip("=")
    raw = _b64.b64decode(value_clean + "==")

    frappe.logger("efris").info(
        f"_extract_aes_key: passowrdDes decoded to {len(raw)} bytes  "
        f"preview(hex): {raw[:16].hex()}"
    )

    if not private_key_pem:
        frappe.log_error(
            f"T104: no private key available to decrypt passowrdDes.\n"
            f"raw length = {len(raw)} bytes\n"
            "The private key must be generated by the taxpayer, the public key\n"
            "registered with EFRIS, and the private key path set in EFRIS Settings.",
            "EFRIS T104 No Private Key",
        )
        frappe.throw(
            "T104: Client private key is not configured.<br><br>"
            "For direct/online EFRIS integration you must:<br>"
            "1. Generate a 2048-bit RSA key pair (use KeyStore Explorer or OpenSSL)<br>"
            "2. Register the <b>public key</b> with EFRIS on the portal<br>"
            "3. Save the <b>private key</b> to a file on this server<br>"
            "4. Set <b>Private Key Path</b> in EFRIS Settings to that file path<br><br>"
            "See <b>Error Log → EFRIS T104 No Private Key</b> for details.",
            title="Private Key Not Configured",
        )

    # ── 1. RSA-PKCS1v1.5  (official URA — SHA1withRSA / PKCS1v1.5) ───────
    try:
        key      = _import_rsa_key(private_key_pem)
        frappe.logger("efris").info(
            f"T104 PKCS1v1.5: key imported OK  "
            f"modulus_bits={key.n.bit_length()}  "
            f"modulus_tail={hex(key.n)[-16:]}"
        )
        sentinel = object()
        result   = PKCS1_v1_5.new(key).decrypt(raw, sentinel)
        if result is sentinel:
            frappe.logger("efris").warning(
                "T104 PKCS1v1.5: decrypt returned sentinel — "
                "private key does not match EFRIS-registered public key, "
                "or EFRIS used different padding."
            )
        else:
            frappe.logger("efris").info(
                f"T104 PKCS1v1.5: decrypt OK  "
                f"plaintext_len={len(result)}  "
                f"plaintext_hex={result.hex()}"
            )
            if len(result) in (16, 24, 32):
                frappe.logger("efris").info("T104: AES key via RSA-PKCS1v1.5 ✓")
                return result
            else:
                frappe.logger("efris").warning(
                    f"T104 PKCS1v1.5: unexpected plaintext length {len(result)} "
                    f"(expected 16/24/32) — not an AES key, trying OAEP."
                )
    except Exception as exc:
        frappe.logger("efris").warning(f"T104 PKCS1v1.5: exception — {exc}")

    # ── 2. RSA-OAEP (fallback) ────────────────────────────────────────────
    try:
        key    = _import_rsa_key(private_key_pem)
        result = _OAEP.new(key).decrypt(raw)
        frappe.logger("efris").info(
            f"T104 OAEP: decrypt OK  plaintext_len={len(result)}  "
            f"plaintext_hex={result.hex()}"
        )
        if len(result) in (16, 24, 32):
            frappe.logger("efris").info("T104: AES key via RSA-OAEP ✓")
            return result
        frappe.logger("efris").warning(
            f"T104 OAEP: unexpected plaintext length {len(result)}"
        )
    except Exception as exc:
        frappe.logger("efris").warning(f"T104 OAEP: exception — {exc}")

    # ── 3. Plain base64 (test/dev environments) ───────────────────────────
    if len(raw) in (16, 24, 32):
        frappe.logger("efris").info(f"T104: AES key is plain base64 ({len(raw)*8}-bit) ✓")
        return raw

    # ── 4. Hex string ─────────────────────────────────────────────────────
    try:
        text = value.strip()
        if len(text) in (32, 48, 64):
            key_bytes = bytes.fromhex(text)
            if len(key_bytes) in (16, 24, 32):
                frappe.logger("efris").info("T104: AES key is hex string ✓")
                return key_bytes
    except Exception:
        pass

    # ── Nothing worked ────────────────────────────────────────────────────
    frappe.log_error(
        f"T104 passowrdDes: RSA decrypt failed with the configured private key.\n"
        f"raw length  = {len(raw)} bytes\n"
        f"raw[:32]    = {raw[:32].hex()}\n"
        "Possible causes:\n"
        "  • Private key does not match the public key registered with EFRIS\n"
        "  • Wrong RSA padding (tried PKCS1v1.5 and OAEP)\n"
        "  • Private key file is corrupt",
        "EFRIS T104 RSA Decrypt Failed",
    )
    frappe.throw(
        "T104: RSA decryption of passowrdDes failed.<br><br>"
        "The private key in <b>EFRIS Settings → Private Key Path</b> does not match "
        "the public key registered with EFRIS.<br><br>"
        "To fix:<br>"
        "1. Check that the private key file path is correct in EFRIS Settings<br>"
        "2. Ensure the public key registered on the EFRIS portal matches your private key<br>"
        "3. Re-generate the key pair and re-register if needed<br><br>"
        "See <b>Error Log → EFRIS T104 RSA Decrypt Failed</b> for raw diagnostic bytes.",
        title="T104 RSA Decrypt Failed",
    )


# ─── Diagnostic: verify keys and T104 raw values ────────────────────────

@frappe.whitelist()
def diagnose_keys() -> dict:
    """
    Full server-side diagnostic: tests RSA key import, decrypts T104 passowrdDes,
    and verifies the resulting AES key against the session file.

    Run from browser console:
        frappe.call('efris_integration.api.auth.diagnose_keys').then(r => console.log(r.message))
    Or from bench:
        bench execute efris_integration.api.auth.diagnose_keys
    """
    import base64 as _b64
    import os
    from efris_integration.utils.encryption import _import_rsa_key
    from Crypto.Cipher import PKCS1_v1_5, PKCS1_OAEP as _OAEP

    client   = EFRISClient()
    settings = client.settings
    km       = client.key_manager

    report = {}

    # ── 1. Private key file ───────────────────────────────────────────────
    priv_path = settings.get("private_key_path") or ""
    report["private_key_path"]   = priv_path
    report["private_key_exists"] = os.path.isfile(priv_path) if priv_path else False

    private_key_pem = ""
    if report["private_key_exists"]:
        try:
            private_key_pem = km.get_private_key()
            report["private_key_length"]  = len(private_key_pem)
            report["private_key_preview"] = private_key_pem[:60]
            rsa_key = _import_rsa_key(private_key_pem)
            report["private_key_rsa_bits"]    = rsa_key.n.bit_length()
            report["private_key_modulus_tail"] = hex(rsa_key.n)[-24:]   # last 24 hex chars
            report["private_key_import"]       = "OK"
        except Exception as exc:
            report["private_key_error"] = str(exc)
    else:
        report["private_key_error"] = "File not found"

    # ── 2. Session AES key file ───────────────────────────────────────────
    sess_path = settings.get("session_key_path") or ""
    report["session_key_path"]   = sess_path
    report["session_key_exists"] = os.path.isfile(sess_path) if sess_path else False

    existing_aes_hex = ""
    if report["session_key_exists"]:
        try:
            key_bytes = km.get_aes_key()
            report["session_key_bytes"] = len(key_bytes)
            report["session_key_hex"]   = key_bytes.hex()
            existing_aes_hex            = key_bytes.hex()
        except Exception as exc:
            report["session_key_error"] = str(exc)

    # ── 3. Call T104 and do RSA decrypt on server ─────────────────────────
    try:
        t104_result = client.call("T104", encrypt=False)
        report["t104_keys"] = list(t104_result.keys())

        pwd = (t104_result.get("passowrdDes", "")
               or t104_result.get("passwordDes", "")).strip()
        report["passowrdDes_length"]        = len(pwd)
        report["passowrdDes_preview_b64"]   = pwd[:80]

        # Decode the base64 ciphertext (strip existing padding first)
        pwd_clean = pwd.rstrip("=")
        raw = _b64.b64decode(pwd_clean + "==")
        report["passowrdDes_decoded_bytes"] = len(raw)
        report["passowrdDes_first16_hex"]   = raw[:16].hex()

        # ── 3a. PKCS1v1.5 decrypt ────────────────────────────────────────
        if private_key_pem:
            try:
                rsa_key  = _import_rsa_key(private_key_pem)
                sentinel = object()
                v15_result = PKCS1_v1_5.new(rsa_key).decrypt(raw, sentinel)
                if v15_result is sentinel:
                    report["pkcs1v15_result"] = "SENTINEL — wrong key or padding; private key does not match EFRIS"
                else:
                    report["pkcs1v15_plaintext_len"] = len(v15_result)
                    report["pkcs1v15_plaintext_hex"] = v15_result.hex()
                    report["pkcs1v15_result"]        = (
                        "OK — valid AES key" if len(v15_result) in (16, 24, 32)
                        else f"UNEXPECTED LENGTH {len(v15_result)} — not a valid AES key size"
                    )
            except Exception as exc:
                report["pkcs1v15_result"] = f"EXCEPTION: {exc}"

            # ── 3b. OAEP decrypt ──────────────────────────────────────────
            try:
                rsa_key    = _import_rsa_key(private_key_pem)
                oaep_result = _OAEP.new(rsa_key).decrypt(raw)
                report["oaep_plaintext_len"] = len(oaep_result)
                report["oaep_plaintext_hex"] = oaep_result.hex()
                report["oaep_result"]        = (
                    "OK — valid AES key" if len(oaep_result) in (16, 24, 32)
                    else f"UNEXPECTED LENGTH {len(oaep_result)}"
                )
            except Exception as exc:
                report["oaep_result"] = f"EXCEPTION: {exc}"
        else:
            report["pkcs1v15_result"] = "SKIPPED — no private key"
            report["oaep_result"]     = "SKIPPED — no private key"

        # ── 3c. Compare decrypted key with session file ───────────────────
        if existing_aes_hex and "pkcs1v15_plaintext_hex" in report:
            decrypted_hex = report["pkcs1v15_plaintext_hex"]
            report["session_key_matches_t104"] = (decrypted_hex == existing_aes_hex)

    except Exception as exc:
        report["t104_error"] = str(exc)

    # ── 4. Log and show ───────────────────────────────────────────────────
    frappe.log_error(frappe.as_json(report, indent=2), "EFRIS Key Diagnostics")

    # Build a human-readable summary
    lines = [
        f"Private key: {report.get('private_key_import','❌')} ({report.get('private_key_rsa_bits','?')} bits)",
        f"Session key file: {report.get('session_key_bytes','?')} bytes  hex={report.get('session_key_hex','?')}",
        f"T104 passowrdDes: {report.get('passowrdDes_decoded_bytes','?')} bytes (ciphertext)",
        f"PKCS1v1.5 decrypt: {report.get('pkcs1v15_result','?')}",
        f"  → plaintext ({report.get('pkcs1v15_plaintext_len','?')} bytes): {report.get('pkcs1v15_plaintext_hex','—')}",
        f"OAEP decrypt: {report.get('oaep_result','?')}",
        f"  → plaintext ({report.get('oaep_plaintext_len','?')} bytes): {report.get('oaep_plaintext_hex','—')}",
        f"Session key matches T104: {report.get('session_key_matches_t104','N/A')}",
    ]

    frappe.msgprint(
        "<pre>" + "\n".join(lines) + "</pre>",
        title="EFRIS Key Diagnostics",
        wide=True,
    )
    return report


# ─── Full Init Sequence ───────────────────────────────────────────────────

@frappe.whitelist()
def full_initialise() -> dict:
    """
    T101 → T103 → T104 in one call.

    CRITICAL ORDER: T103 MUST run before T104.
    On the EFRIS server, calling T103 after T104 resets the session and
    invalidates the AES session key that was just obtained from T104.
    Running T103 first (to initialise/sign-in) and then T104 (to fetch
    the fresh session key) guarantees the saved key is valid.
    """
    results = {}

    frappe.publish_progress(10, title="EFRIS Init", description="T101 — Testing connection…")
    results["server_time"] = get_server_time()

    # T103 FIRST — sign in / get device info (may reset the EFRIS session)
    frappe.publish_progress(40, title="EFRIS Init", description="T103 — Sign in…")
    results["t103"] = sign_in()

    # T104 LAST — get fresh session AES key after T103 has settled the session
    frappe.publish_progress(75, title="EFRIS Init", description="T104 — Getting session key…")
    results["t104"] = get_symmetric_key()

    frappe.publish_progress(100, title="EFRIS Init", description="Done ✅")

    t104_bits = results["t104"].get("key_bits", "?")
    frappe.msgprint(
        f"🎉 EFRIS initialised! Server time: {results['server_time']}<br>"
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
