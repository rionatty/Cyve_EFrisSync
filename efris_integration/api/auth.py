"""
EFRIS Authentication  —  T101 → T102 → T103 → T104  (per-company)

Mirrors exactly: ClsSBOAddOn.cs, URASetting.cs, T102.cs, T103.cs, T104.cs

Key lifecycle (from SAP source):
  T101 → Test connection, returns T101.CurrentTime
  T102 → Returns T102.clientPriKey (AES-encrypted) + T102.serverPubKey
           AES key = TaxpayerID[:10] + date("ddMMyy"), padded/truncated to 16 bytes
           Decrypted private key saved to private_key field in EFRIS Settings
           Server public key saved to public_key field in EFRIS Settings
  T103 → Returns T103: Device, Taxpayer, TaxpayerBranch, TaxTypes (no password sent)
           TIN + TaxpayerID auto-updated from response
  T104 → Returns T104.passowrdDes (note: SAP source typo — double 'o')
           RSA-decrypted with client private key → session AES key
           Saved to session_aes_key field in EFRIS Settings (base64)

Keys are stored directly in the database (EFRIS Settings fields):
  private_key      — paste content of PrivateKey.txt   (base64 PKCS#8 or PEM)
  public_key       — paste content of PublicKey.txt     (base64 or PEM)
  session_aes_key  — auto-filled by T104 (base64, e.g. x/SVvBh4S49ddPNX+nXQYg==)

No username/password is stored or sent. Authentication is entirely via
the RSA key-exchange in T102/T104.

All public functions accept a `company` parameter — EFRIS Settings are per-company.
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
def get_server_time(company: str) -> str:
    """T101 — Mirrors TestEFRISConnection() in URASetting.cs."""
    client = EFRISClient(company=company)
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
def client_init(company: str) -> dict:
    """
    T102 — Download server RSA public key + AES-encrypted client private key.

    URA documentation (T102 flow):
        1. Client sends T102 with empty data block (no OTP needed for AP04).
        2. EFRIS encrypts the private key:
             - With keyTable returned: decrypt using keyTable as AES key (white-box)
             - Without keyTable (AP04): existing valid private key is kept
        3. Returns: clientPriKey, serverPubKey, keyTable (may be absent for AP04)
        4. Client decrypts clientPriKey and saves both keys to the database.

    If decryption fails but the private key field already exists and is valid
    (i.e. T104 works), we log a warning and keep the existing key rather
    than failing — the key is already correctly set up.
    """
    client   = EFRISClient(company=company)
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
        f"T102  company={company}  keyTable={'present' if key_table else 'MISSING'}\n"
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
            "✅ T102: Server public key and client private key saved to EFRIS Settings.",
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
            f"T102 clientPriKey decryption failed (keyTable missing).\n"
            f"Existing private key in database is valid — kept as-is.\n"
            f"Error: {decrypt_error}\n"
            f"clientPriKey[:120]: {encrypted_priv_key[:120]}",
            "EFRIS T102 Decrypt Warning",
        )
        frappe.msgprint(
            "⚠️ T102: Server public key updated.<br><br>"
            "The EFRIS server did not return a decryptable private key "
            "(<code>keyTable</code> missing).<br>"
            "Your existing private key in EFRIS Settings is still valid — no change made.<br><br>"
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
def sign_in(company: str) -> dict:
    """
    T103 — Mirrors T103.cs response model.

    SAP sends empty payload; EFRIS returns:
        T103.Device, T103.Taxpayer, T103.TaxpayerBranch, T103.TaxTypes, …

    NO username/password is sent. Authentication is via the RSA key exchange.
    TIN and TaxpayerID in settings are updated from the response if present.
    """
    client   = EFRISClient(company=company)
    settings = client.settings

    result = client.call("T103", encrypt=False)

    # Extract taxpayer info from T103 response and persist to settings
    taxpayer = result.get("taxpayer", {})
    if isinstance(taxpayer, dict):
        tin   = taxpayer.get("tin", "") or taxpayer.get("taxpayerTin", "")
        tp_id = taxpayer.get("taxpayerId", "") or taxpayer.get("taxPayerID", "")
        if tin:
            settings.db_set("tin", tin, update_modified=False)
        if tp_id:
            settings.db_set("taxpayer_id", tp_id, update_modified=False)

    frappe.msgprint("✅ T103: Device / taxpayer info received from EFRIS.", indicator="green")
    return {"status": "OK", "taxpayer": taxpayer}


# ─── T104: Get Session AES Key ────────────────────────────────────────────

@frappe.whitelist()
def get_symmetric_key(company: str) -> dict:
    """
    T104 — Verify or refresh the session AES key.

    Strategy:
      0. Test the EXISTING session key (from session_aes_key DB field) against T126.
         If it still works → return immediately without calling T104.
         This preserves a valid key even when the private key is
         incorrect (RSA decrypt of passowrdDes gives wrong results).

      1. If the existing key is expired (SESSION_EXPIRED) or missing:
         Call T104 → get passowrdDes → build candidate AES keys
         (PKCS1v1.5, OAEP, raw slices, hashes) → probe each with T126.

      2. Save the first candidate whose T126 decrypt succeeds.

    NOTE: If all T104 candidates fail it means the private key in EFRIS Settings
    does NOT match the public key registered with EFRIS.
    The long-term fix is to re-register a new key pair with EFRIS.
    """
    import base64 as _b64
    import hashlib

    client = EFRISClient(company=company)

    # ── Step 0: test the existing session key ─────────────────────────────
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
                doc_type="__key_check__", doc_name="existing",
            )
            # Existing key decrypts T126 — no T104 call needed
            frappe.logger("efris").info(
                f"T104 skipped [{company}] — existing session key still valid: {existing_key.hex()}"
            )
            frappe.msgprint(
                f"✅ T104: Existing session key ({len(existing_key)*8}-bit) is still valid.",
                indicator="green",
            )
            return {"status": "AES Key Valid (existing)", "key_bits": len(existing_key) * 8}

        except EFRISAPIError as exc:
            if exc.code != "SESSION_EXPIRED":
                # AES decrypt succeeded; EFRIS returned a non-00 code for other reasons
                frappe.logger("efris").info(
                    f"T104 skipped [{company}] — existing key OK (T126 [{exc.code}] but decrypt passed)"
                )
                frappe.msgprint(
                    f"✅ T104: Existing session key ({len(existing_key)*8}-bit) is still valid.",
                    indicator="green",
                )
                return {"status": "AES Key Valid (existing)", "key_bits": len(existing_key) * 8}
            # SESSION_EXPIRED → key is stale, fall through to T104
            frappe.logger("efris").info(
                f"T104 [{company}]: existing key expired — fetching fresh key via T104"
            )
            client._aes_key = None

        except Exception:
            # Network error — can't check; fall through to T104
            client._aes_key = None

    # ── Step 1: call T104 to get fresh passowrdDes ────────────────────────
    try:
        private_key_pem = client.key_manager.get_private_key()
    except Exception as e:
        frappe.log_error(
            f"T104 [{company}]: private key read error — {e}\n"
            f"Check the 'Private Key' field in EFRIS Settings for company '{company}'.",
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
        f"T104 [{company}] passowrdDes: {len(raw)} bytes  first16={raw[:16].hex()}"
    )

    # ── Build every plausible candidate AES key ────────────────────────────
    candidates = _build_aes_key_candidates(raw, private_key_pem)
    frappe.logger("efris").info(
        f"T104 [{company}]: {len(candidates)} AES key candidates to probe: "
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
            client.set_aes_key(key_bytes)       # persist to session_aes_key DB field
            client._aes_key = key_bytes          # keep in cache
            frappe.logger("efris").info(
                f"T104 ✅ [{company}] AES key confirmed by T126 probe [{label}]: "
                f"{len(key_bytes)*8}-bit  {key_bytes.hex()}"
            )
            frappe.msgprint(
                f"✅ T104: Session AES key ({len(key_bytes)*8}-bit) saved to EFRIS Settings.",
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
                f"T104 ✅ [{company}] AES key confirmed [{label}] "
                f"(T126 returned [{exc.code}] but decryption succeeded): {key_bytes.hex()}"
            )
            frappe.msgprint(
                f"✅ T104: Session AES key ({len(key_bytes)*8}-bit) saved to EFRIS Settings.",
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
            f"T104 [{company}]: all T126 probes hit network errors. "
            f"Using first candidate as fallback.\n"
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
        f"T104 [{company}]: all {len(candidates)} AES key candidates failed T126 SESSION_EXPIRED.\n"
        f"passowrdDes raw[:32]: {raw[:32].hex()}\n"
        f"private_key loaded: {'yes' if private_key_pem else 'NO'}\n"
        f"Candidates tried:\n{candidates_log}",
        "EFRIS T104 All Candidates Failed",
    )
    frappe.db.commit()

    frappe.throw(
        "T104: None of the candidate AES keys could decrypt EFRIS responses.<br><br>"
        "<b>Root cause</b>: The private key in the <b>Private Key</b> field in EFRIS Settings "
        "does NOT match the public key registered with EFRIS.<br><br>"
        "<b>How to fix (Option A — generate a new key pair)</b>:<br>"
        "1. Click <b>Generate Key Pair</b> in EFRIS Settings — a new 2048-bit RSA key pair is generated.<br>"
        "2. Copy the public key shown in the dialog.<br>"
        "3. Log into the EFRIS portal → Device Management → Update Public Key.<br>"
        "4. Paste the public key and submit.<br>"
        "5. Once EFRIS activates it, click <b>Full Initialise</b>.<br><br>"
        "<b>How to fix (Option B — paste existing keys)</b>:<br>"
        "1. Paste your <b>PrivateKey.txt</b> content into the Private Key field.<br>"
        "2. Paste your <b>PublicKey.txt</b> content into the EFRIS Server Public Key field.<br>"
        "3. Paste your <b>SessionAESKey.txt</b> content into the Session AES Key field.<br>"
        "4. Save EFRIS Settings, then click <b>Test Session Key</b>.<br><br>"
        "See <b>Error Log → EFRIS T104 All Candidates Failed</b> for raw bytes.",
        title="T104: Private Key Mismatch",
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

    seen: set = set()
    out: list = []

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


# ─── Diagnostic: verify keys and T104 raw values ────────────────────────

@frappe.whitelist()
def diagnose_keys(company: str) -> dict:
    """
    Full server-side diagnostic: tests RSA key import, decrypts T104 passowrdDes,
    and verifies the resulting AES key against the session_aes_key DB field.

    Run from browser console:
        frappe.call('efris_integration.api.auth.diagnose_keys',
                    {company: 'My Company'}).then(r => console.log(r.message))
    """
    import base64 as _b64
    from efris_integration.utils.encryption import _import_rsa_key
    from Crypto.Cipher import PKCS1_v1_5, PKCS1_OAEP as _OAEP

    client   = EFRISClient(company=company)
    settings = client.settings
    km       = client.key_manager

    report = {"company": company}

    # ── 1. Private key (from DB field) ───────────────────────────────────
    priv_field_len = len((settings.get("private_key") or "").strip())
    report["private_key_field_length"] = priv_field_len
    report["private_key_configured"]   = priv_field_len > 0

    private_key_pem = ""
    if priv_field_len > 0:
        try:
            private_key_pem = km.get_private_key()
            report["private_key_length"]  = len(private_key_pem)
            report["private_key_preview"] = private_key_pem[:60]
            rsa_key = _import_rsa_key(private_key_pem)
            report["private_key_rsa_bits"]    = rsa_key.n.bit_length()
            report["private_key_modulus_tail"] = hex(rsa_key.n)[-24:]
            report["private_key_import"]       = "OK"
        except Exception as exc:
            report["private_key_error"] = str(exc)
    else:
        report["private_key_error"] = "Private Key field is empty in EFRIS Settings"

    # ── 2. Session AES key (from DB field) ────────────────────────────────
    sess_b64 = (settings.get("session_aes_key") or "").strip()
    report["session_key_configured"] = bool(sess_b64)
    report["session_key_field_preview"] = sess_b64[:40] if sess_b64 else "(empty)"

    existing_aes_hex = ""
    if sess_b64:
        try:
            key_bytes = km.get_aes_key()
            report["session_key_bytes"] = len(key_bytes)
            report["session_key_hex"]   = key_bytes.hex()
            report["session_key_b64"]   = _b64.b64encode(key_bytes).decode()
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

        pwd_clean = pwd.rstrip("=")
        raw = _b64.b64decode(pwd_clean + "==")
        report["passowrdDes_decoded_bytes"] = len(raw)
        report["passowrdDes_first16_hex"]   = raw[:16].hex()

        # ── 3a. PKCS1v1.5 decrypt ────────────────────────────────────────
        if private_key_pem:
            try:
                rsa_key    = _import_rsa_key(private_key_pem)
                sentinel   = object()
                v15_result = PKCS1_v1_5.new(rsa_key).decrypt(raw, sentinel)
                if v15_result is sentinel:
                    report["pkcs1v15_result"] = "SENTINEL — wrong key or padding"
                else:
                    report["pkcs1v15_plaintext_len"] = len(v15_result)
                    report["pkcs1v15_plaintext_hex"] = v15_result.hex()
                    report["pkcs1v15_result"]        = (
                        "OK — valid AES key" if len(v15_result) in (16, 24, 32)
                        else f"UNEXPECTED LENGTH {len(v15_result)}"
                    )
            except Exception as exc:
                report["pkcs1v15_result"] = f"EXCEPTION: {exc}"

            try:
                rsa_key     = _import_rsa_key(private_key_pem)
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
            report["pkcs1v15_result"] = "SKIPPED — Private Key field is empty"
            report["oaep_result"]     = "SKIPPED — Private Key field is empty"

        if existing_aes_hex and "pkcs1v15_plaintext_hex" in report:
            report["session_key_matches_t104"] = (
                report["pkcs1v15_plaintext_hex"] == existing_aes_hex
            )

    except Exception as exc:
        report["t104_error"] = str(exc)

    # ── 4. Log and show ───────────────────────────────────────────────────
    frappe.log_error(frappe.as_json(report, indent=2), "EFRIS Key Diagnostics")

    lines = [
        f"Company: {company}",
        f"Private key (DB field): {'✅ configured' if priv_field_len > 0 else '❌ EMPTY'}",
        f"  → RSA import: {report.get('private_key_import','❌')} "
          f"({report.get('private_key_rsa_bits','?')} bits)",
        f"Session AES key (DB field): {'✅ configured' if sess_b64 else '❌ EMPTY'}",
        f"  → {report.get('session_key_bytes','?')} bytes  hex={report.get('session_key_hex','?')}",
        f"  → b64={report.get('session_key_b64','?')}",
        f"T104 passowrdDes: {report.get('passowrdDes_decoded_bytes','?')} bytes (ciphertext)",
        f"PKCS1v1.5 decrypt: {report.get('pkcs1v15_result','?')}",
        f"  → plaintext ({report.get('pkcs1v15_plaintext_len','?')} bytes): "
          f"{report.get('pkcs1v15_plaintext_hex','—')}",
        f"OAEP decrypt: {report.get('oaep_result','?')}",
        f"  → plaintext ({report.get('oaep_plaintext_len','?')} bytes): "
          f"{report.get('oaep_plaintext_hex','—')}",
        f"Session key matches T104: {report.get('session_key_matches_t104','N/A')}",
    ]

    frappe.msgprint(
        "<pre>" + "\n".join(lines) + "</pre>",
        title="EFRIS Key Diagnostics",
        wide=True,
    )
    return report


# ─── Session Key Recovery Helpers ────────────────────────────────────────

@frappe.whitelist()
def set_session_key(company: str, key_b64: str) -> dict:
    """
    Manually write a base64-encoded AES session key to the session_aes_key DB field.

    Use this when:
      - T104 probes all fail (private key mismatch)
      - You have obtained the correct session key from EFRIS support or
        from a previously-working system (e.g. the SAP B1 instance)
      - You have the raw SessionAESKey.txt content to paste in

    The key must be exactly 16, 24, or 32 bytes when base64-decoded.
    """
    import base64 as _b64

    key_b64 = (key_b64 or "").strip()
    if not key_b64:
        frappe.throw("key_b64 is required.", title="Missing Key")

    try:
        key_bytes = _b64.b64decode(key_b64.rstrip("=") + "==")
    except Exception as exc:
        frappe.throw(f"Invalid base64: {exc}", title="Bad Key Format")

    if len(key_bytes) not in (16, 24, 32):
        frappe.throw(
            f"Decoded key is {len(key_bytes)} bytes — must be 16, 24, or 32 bytes (128/192/256-bit AES).",
            title="Bad Key Length",
        )

    client = EFRISClient(company=company)
    client.set_aes_key(key_bytes)

    # Immediately verify the key against T126
    client._aes_key = key_bytes
    verified = False
    try:
        client.call("T126", payload=None, encrypt=False,
                    doc_type="__key_set__", doc_name="manual")
        verified = True
    except EFRISAPIError as exc:
        if exc.code != "SESSION_EXPIRED":
            verified = True   # decrypt succeeded; EFRIS returned other error
    except Exception:
        pass   # network issue — key is saved, can't verify now

    if verified:
        frappe.msgprint(
            f"✅ Session AES key ({len(key_bytes)*8}-bit) saved to EFRIS Settings and <b>verified</b> "
            f"— T126 decrypts successfully.",
            indicator="green",
            title="Session Key Saved",
        )
    else:
        frappe.msgprint(
            f"⚠️ Session AES key ({len(key_bytes)*8}-bit) saved to EFRIS Settings, "
            "but <b>T126 verification failed</b> — "
            "the key may be wrong or the server session has changed.",
            indicator="orange",
            title="Session Key Saved (unverified)",
        )

    return {"status": "saved", "verified": verified, "key_bits": len(key_bytes) * 8}


@frappe.whitelist()
def generate_key_pair(company: str) -> dict:
    """
    Generate a fresh 2048-bit RSA key pair on the server.

    Saves the private key to the private_key field in EFRIS Settings (DB).
    Returns the public key (PEM) so you can register it with the EFRIS portal.

    Steps after calling this:
      1. Copy the public key shown below.
      2. Log into the EFRIS portal → Device Management → Update Public Key.
      3. Paste the public key and submit.
      4. Once EFRIS activates it, click "Full Initialise" or just "T104".
    """
    from Crypto.PublicKey import RSA as _RSA

    frappe.logger("efris").info(
        f"generate_key_pair [{company}]: generating 2048-bit RSA key pair…"
    )

    key      = _RSA.generate(2048)
    priv_pem = key.export_key("PEM").decode("utf-8")
    pub_pem  = key.publickey().export_key("PEM").decode("utf-8")

    client = EFRISClient(company=company)
    km     = client.key_manager

    km.save_private_key(priv_pem)

    try:
        km.save_public_key(pub_pem)
    except Exception:
        pass   # optional — not fatal if something goes wrong saving pub key

    frappe.log_error(
        f"New RSA key pair generated for [{company}].\n"
        f"Public key (register this with EFRIS portal):\n{pub_pem}\n\n"
        f"Private key saved to EFRIS Settings (private_key field).",
        "EFRIS New Key Pair Generated",
    )
    frappe.db.commit()

    frappe.msgprint(
        f"🔑 New 2048-bit RSA key pair generated for <b>{company}</b>.<br><br>"
        f"<b>Private key</b> saved to EFRIS Settings (Private Key field).<br><br>"
        f"<b>Public key</b> (register with EFRIS portal → Device Management):<br>"
        f"<pre style='font-size:11px;word-break:break-all;'>{pub_pem}</pre><br><br>"
        f"After registering the public key on the EFRIS portal, click "
        f"<b>Full Initialise</b> to obtain a fresh session key.",
        title="Key Pair Generated",
        wide=True,
    )
    return {"status": "OK", "public_key": pub_pem}


@frappe.whitelist()
def test_session_key(company: str) -> dict:
    """
    Test whether the current session AES key in the session_aes_key DB field
    can decrypt T126. Shows a green tick if it works, red cross if it fails.
    """
    import base64 as _b64

    client   = EFRISClient(company=company)
    km       = client.key_manager
    settings = client.settings

    sess_b64 = (settings.get("session_aes_key") or "").strip()
    if not sess_b64:
        frappe.msgprint(
            "❌ Session AES Key field is empty in EFRIS Settings.<br>"
            "Click <b>Full Initialise</b> or paste the SessionAESKey.txt content manually.",
            indicator="red",
            title="No Session Key",
        )
        return {"status": "missing"}

    try:
        key_bytes = km.get_aes_key()
    except Exception as exc:
        frappe.msgprint(
            f"❌ Cannot parse session key: {exc}",
            indicator="red",
            title="Session Key Parse Error",
        )
        return {"status": "parse_error", "error": str(exc)}

    client._aes_key = key_bytes

    try:
        client.call("T126", payload=None, encrypt=False,
                    doc_type="__key_test__", doc_name="test")
        frappe.msgprint(
            f"✅ Session key ({len(key_bytes)*8}-bit) is VALID — T126 decrypts successfully.<br>"
            f"Key hex: <code>{key_bytes.hex()}</code><br>"
            f"Key b64: <code>{_b64.b64encode(key_bytes).decode()}</code>",
            indicator="green",
            title="Session Key Valid",
        )
        return {"status": "valid", "key_bits": len(key_bytes) * 8, "key_hex": key_bytes.hex()}

    except EFRISAPIError as exc:
        if exc.code == "SESSION_EXPIRED":
            frappe.msgprint(
                f"❌ Session key ({len(key_bytes)*8}-bit) is EXPIRED / WRONG.<br><br>"
                f"Key hex: <code>{key_bytes.hex()}</code><br><br>"
                f"<b>Options to recover</b>:<br>"
                f"1. Click <b>Full Initialise</b> (T101 → T103 → T104) to get a fresh session key.<br>"
                f"2. Click <b>Generate Key Pair</b> if the private key no longer matches EFRIS.<br>"
                f"3. Paste a known-good base64 key directly into the Session AES Key field.",
                indicator="red",
                title="Session Key Invalid",
            )
            return {"status": "expired", "key_hex": key_bytes.hex()}
        else:
            frappe.msgprint(
                f"✅ Session key ({len(key_bytes)*8}-bit) is VALID "
                f"(T126 [{exc.code}] but decrypt passed).<br>"
                f"Key hex: <code>{key_bytes.hex()}</code>",
                indicator="green",
                title="Session Key Valid",
            )
            return {"status": "valid", "key_bits": len(key_bytes) * 8, "efris_code": exc.code}

    except Exception as exc:
        frappe.msgprint(
            f"⚠️ Network error testing session key: {exc}",
            indicator="orange",
            title="Test Inconclusive",
        )
        return {"status": "network_error", "error": str(exc)}


# ─── Full Init Sequence ───────────────────────────────────────────────────

@frappe.whitelist()
def full_initialise(company: str) -> dict:
    """
    T101 → T103 → T104 in one call for the given company.

    CRITICAL ORDER: T103 MUST run before T104.
    On the EFRIS server, calling T103 after T104 resets the session and
    invalidates the AES session key that was just obtained from T104.
    """
    results = {}

    frappe.publish_progress(10, title="EFRIS Init", description="T101 — Testing connection…")
    results["server_time"] = get_server_time(company)

    frappe.publish_progress(40, title="EFRIS Init", description="T103 — Sign in…")
    results["t103"] = sign_in(company)

    frappe.publish_progress(75, title="EFRIS Init", description="T104 — Verifying session key…")
    results["t104"] = get_symmetric_key(company)

    frappe.publish_progress(100, title="EFRIS Init", description="Done ✅")

    frappe.msgprint(
        f"🎉 EFRIS initialised for <b>{company}</b>! "
        f"Server time: {results['server_time']}<br>"
        f"Session AES key saved to EFRIS Settings. Ready to submit documents.",
        indicator="green",
        title="EFRIS Initialised",
    )
    return results


# ─── Scheduler: refresh session for all configured companies ──────────────

def refresh_session_if_needed():
    """
    Called hourly by the scheduler.
    Iterates all EFRIS Settings records and re-runs T103 + T104
    for any company whose session AES key field is missing or stale.
    """
    companies = frappe.get_all("EFRIS Settings", fields=["name", "api_url", "session_aes_key"])
    for row in companies:
        company = row["name"]
        api_url = row.get("api_url") or ""

        if not api_url:
            continue   # not configured

        try:
            sess_b64 = (row.get("session_aes_key") or "").strip()

            if not sess_b64:
                # Session key field is empty — try a full init
                frappe.logger("efris").info(
                    f"EFRIS [{company}]: session key empty — auto-initialising."
                )
                sign_in(company)
                get_symmetric_key(company)
                continue

            # Session key exists — test it; refresh only if expired
            client = EFRISClient(company=company)
            try:
                from efris_integration.api.key_manager import _parse_aes_key_b64
                key_bytes = _parse_aes_key_b64(sess_b64)
                if not key_bytes:
                    raise ValueError("Cannot parse AES key from DB field")
                client._aes_key = key_bytes
                client.call(
                    "T126", payload=None, encrypt=False,
                    doc_type="__session_check__", doc_name=company,
                )
                # Key still valid — nothing to do
                frappe.logger("efris").info(
                    f"EFRIS [{company}]: session key still valid — skipping refresh."
                )
            except EFRISAPIError as exc:
                if exc.code == "SESSION_EXPIRED":
                    frappe.logger("efris").info(
                        f"EFRIS [{company}]: session key expired — refreshing."
                    )
                    sign_in(company)
                    get_symmetric_key(company)
                # Other EFRIS errors (key ok, just non-00 response) — leave as-is

        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"EFRIS Session Refresh Failed [{company}]",
            )
