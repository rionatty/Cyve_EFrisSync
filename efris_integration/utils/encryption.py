"""
Encryption utilities mirroring SAP B1 EncryptionHelper.cs

EFRIS uses:
  - AES-128/256 ECB mode with PKCS7 padding for data encryption  (EncryptionHelper.cs)
  - RSA-OAEP for key exchange (T104 session key decrypt)
  - RSA-SHA1-PKCS1v1.5 for request signing  (ExchangeRateForm.cs CngKey + RSACng.SignData)
  - GZip for optional compression
  - Base64 for wire encoding
"""

import base64
import gzip
import json

from Crypto.Cipher import AES, PKCS1_OAEP
from Crypto.Hash import SHA1
from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15
from Crypto.Util.Padding import pad, unpad


def aes_encrypt(data: bytes, key: bytes) -> bytes:
    cipher = AES.new(key, AES.MODE_ECB)
    return cipher.encrypt(pad(data, AES.block_size))


def aes_decrypt(data: bytes, key: bytes) -> bytes:
    cipher = AES.new(key, AES.MODE_ECB)
    return unpad(cipher.decrypt(data), AES.block_size)


def _import_rsa_key(key_data: str):
    """
    Import an RSA key tolerating every format EFRIS/SAP B1 may produce:
      1. Standard PEM  (-----BEGIN [RSA] PRIVATE KEY-----)
      2. Base64-encoded DER without PEM headers  (raw PKCS#8 blob from SAP CNG export)
      3. DER wrapped in PKCS#8 PEM headers  (most pycryptodome-friendly)
      4. DER wrapped in PKCS#1 RSA PEM headers
    Raises ValueError with a helpful message if nothing works.
    """
    text = (key_data or "").strip()

    # 1. Try as-is — covers PEM and raw DER bytes that happen to be valid UTF-8
    try:
        return RSA.import_key(text)
    except (ValueError, IndexError, TypeError):
        pass

    # 2. Try base64-decoded DER (SAP B1 CNG Pkcs8PrivateBlob exported as base64)
    try:
        der = base64.b64decode(text + "==")
        return RSA.import_key(der)
    except Exception:
        pass

    # 3. Wrap in PKCS#8 PEM header (most common for CNG-generated keys)
    try:
        pem = f"-----BEGIN PRIVATE KEY-----\n{text}\n-----END PRIVATE KEY-----"
        return RSA.import_key(pem)
    except Exception:
        pass

    # 4. Wrap in PKCS#1 RSA PEM header
    try:
        pem = f"-----BEGIN RSA PRIVATE KEY-----\n{text}\n-----END RSA PRIVATE KEY-----"
        return RSA.import_key(pem)
    except Exception:
        pass

    raise ValueError(
        "RSA key format is not supported.<br><br>"
        "The Private Key in EFRIS Settings is in an unrecognised format.<br>"
        "Run <b>Full Initialise</b> to regenerate it, or paste a valid "
        "PEM or base64-encoded PKCS#8 private key into the Private Key field.<br>"
        f"Key preview: <code>{text[:80]}</code>"
    )


def rsa_decrypt(encrypted_b64: str, private_key_pem: str) -> bytes:
    """
    Decrypt RSA ciphertext with the client private key.
    Tries PKCS1v1.5 first (used by EFRIS Uganda T104), then OAEP as fallback.
    """
    from Crypto.Cipher import PKCS1_v1_5
    key        = _import_rsa_key(private_key_pem)
    ciphertext = base64.b64decode(encrypted_b64 + "==")

    # 1. PKCS1v1.5 — EFRIS Uganda T104 passowrdDes
    try:
        _sentinel = object()
        result = PKCS1_v1_5.new(key).decrypt(ciphertext, _sentinel)
        if result is not _sentinel and result:
            return result
    except Exception:
        pass

    # 2. OAEP fallback
    try:
        return PKCS1_OAEP.new(key).decrypt(ciphertext)
    except Exception:
        pass

    raise ValueError(
        "RSA decryption failed (tried PKCS1v1.5 and OAEP).<br>"
        "The EFRIS session key cannot be decrypted — the Private Key in EFRIS Settings "
        "may not match the current EFRIS server session.<br>"
        "Run <b>Full Initialise</b> from EFRIS Settings to regenerate keys."
    )


def rsa_encrypt(data: bytes, public_key_pem: str) -> str:
    key = _import_rsa_key(public_key_pem)
    cipher = PKCS1_OAEP.new(key)
    return base64.b64encode(cipher.encrypt(data)).decode()


def encode_request_data(payload: dict, aes_key: bytes, compress: bool = False) -> dict:
    """
    Serialize → optionally gzip → AES-ECB encrypt → base64.
    Returns {"content": "...", "dataDescription": {"zipCode": "0|1", "encryptCode": "0|2"}}
    """
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    zip_code = "0"
    if compress:
        raw = gzip.compress(raw)
        zip_code = "1"

    encrypted = aes_encrypt(raw, aes_key)
    content = base64.b64encode(encrypted).decode("utf-8")

    return {
        "content": content,
        "dataDescription": {
            "zipCode": zip_code,
            "encryptCode": "2",
        },
    }


def decode_response_data(data: dict, aes_key: bytes) -> dict:
    """
    Base64 decode → AES-ECB decrypt → optionally gunzip → JSON parse.
    Handles encryptCode 0 (plain), 2 (AES), zipCode 0 (raw), 1 (gzip).
    """
    content = data.get("content", "")
    desc = data.get("dataDescription", {})
    encrypt_code = str(desc.get("encryptCode", "2"))
    zip_code = str(desc.get("zipCode", "0"))

    raw = base64.b64decode(content)

    if encrypt_code == "2":
        raw = aes_decrypt(raw, aes_key)

    if zip_code == "1":
        raw = gzip.decompress(raw)

    text = raw.decode("utf-8").strip()

    # URA sometimes returns an empty body on success (no payload needed).
    # json.loads("") raises JSONDecodeError — return {} instead.
    if not text:
        return {}

    return json.loads(text)


def rsa_sign(content_b64: str, private_key_pem: str) -> str:
    """
    RSA SHA1 PKCS1v1.5 signature of the base64-encoded content string.

    Mirrors ExchangeRateForm.cs exactly:
        CngKey.Import(privateKey, Pkcs8PrivateBlob)
        RSACng.SignData(UTF8.GetBytes(contentB64), SHA1, PKCS1)

    Args:
        content_b64: the base64 string of the AES-encrypted payload
        private_key_pem: client private key (PEM, base64 DER, or raw DER)
    Returns:
        base64-encoded signature string
    """
    key = _import_rsa_key(private_key_pem)
    h   = SHA1.new(content_b64.encode("utf-8"))
    sig = pkcs1_15.new(key).sign(h)
    return base64.b64encode(sig).decode("utf-8")


def derive_private_key_aes_key(taxpayer_id: str, init_date: str) -> bytes:
    """
    SAP B1 derives the AES key for the private key file as:
      TaxpayerID.Substring(0,10) + date formatted as "ddMMyy"
    Returns a 16-byte key (pad/truncate to 16).
    """
    base = (taxpayer_id[:10] + init_date).encode("utf-8")
    return base[:16].ljust(16, b"\x00")


def try_decrypt_private_key(
    encrypted_b64: str,
    taxpayer_id: str,
    tin: str = "",
    key_table: str = "",
    otp: str = "",
    device_no: str = "",
) -> str:
    """
    Decrypt the T102 clientPriKey (white-box AES-128-ECB encrypted private key).

    URA documentation: EFRIS encrypts clientPriKey using a white-box program and
    returns the decryption key in the `keyTable` field of the T102 response.
    The keyTable value is the AES key (base64-encoded) for decrypting clientPriKey.

    Decryption attempts (in order):
      0. OTP sent in T102 request (padded to 16 bytes) — used when keyTable absent
      1. keyTable as AES key — base64 decoded directly (official URA white-box)
      2. keyTable bytes truncated/padded to 16, 24, or 32 bytes
      3. deviceNo padded to 16 bytes
      4. taxpayerID alone padded to 16 bytes
      5. taxpayer_id[:10] + ddMMyy    ← SAP B1 EncryptionHelper.cs
      6. taxpayer_id[:10] + ddMMyyyy, yyyyMMdd, MMddyy
      7. Same date formats with TIN
      8. clientPriKey is not encrypted — direct PEM or DER import
      9. clientPriKey base64-decoded twice (double-encoded PEM)

    Raises ValueError with full diagnostic info if all attempts fail.
    """
    from datetime import datetime
    from Crypto.Cipher import AES as _AES
    from Crypto.Util.Padding import unpad as _unpad

    import frappe as _frappe

    raw = base64.b64decode(encrypted_b64.strip().rstrip("=") + "==")
    today = datetime.now()

    _frappe.logger("efris").info(
        f"try_decrypt_private_key: encrypted_b64 length={len(encrypted_b64)}  "
        f"raw length={len(raw)} bytes  "
        f"raw[:16] hex={raw[:16].hex()}  "
        f"keyTable={'present' if key_table else 'MISSING'}  "
        f"otp={'present' if otp else 'not sent'}"
    )

    def _try_decrypt(label: str, aes_key: bytes) -> str | None:
        """Try AES-128/192/256-ECB decrypt with both PKCS7 and zero-strip unpadding."""
        if len(aes_key) not in (16, 24, 32):
            return None
        try:
            cipher    = _AES.new(aes_key, _AES.MODE_ECB)
            decrypted = cipher.decrypt(raw)

            # PKCS7 unpad
            try:
                plain = _unpad(decrypted, _AES.block_size, style="pkcs7")
                text  = plain.decode("utf-8", errors="replace").strip()
                if _looks_like_private_key(text):
                    _frappe.logger("efris").info(f"T102 clientPriKey OK — {label} (PKCS7)")
                    return text
            except Exception:
                pass

            # NUL-byte / zero-strip padding
            try:
                plain = decrypted.rstrip(b"\x00")
                text  = plain.decode("utf-8", errors="replace").strip()
                if _looks_like_private_key(text):
                    _frappe.logger("efris").info(f"T102 clientPriKey OK — {label} (zero-pad)")
                    return text
            except Exception:
                pass

        except Exception:
            pass
        return None

    tried: list[str] = []

    # ── 1. keyTable (official URA white-box key) ──────────────────────────────
    if key_table:
        kt_clean = key_table.strip().rstrip("=")
        try:
            kt_raw = base64.b64decode(kt_clean + "==")
            _frappe.logger("efris").info(
                f"T102 keyTable decoded: {len(kt_raw)} bytes  hex={kt_raw[:16].hex()}"
            )
            result = _try_decrypt(f"keyTable({len(kt_raw)}B)", kt_raw)
            if result:
                return result
            tried.append(f"keyTable-{len(kt_raw)}B")

            for sz in (16, 24, 32):
                if len(kt_raw) != sz:
                    result = _try_decrypt(f"keyTable[:{sz}]", (kt_raw + b"\x00" * sz)[:sz])
                    if result:
                        return result
                    tried.append(f"keyTable[:{sz}]")
        except Exception as exc:
            tried.append(f"keyTable-decode-error({exc})")

        # keyTable as raw UTF-8 bytes, padded to 16/24/32
        try:
            kt_utf8 = key_table.strip().encode("utf-8")
            for sz in (16, 24, 32):
                result = _try_decrypt(f"keyTable-utf8[:{sz}]", (kt_utf8 + b"\x00" * sz)[:sz])
                if result:
                    return result
                tried.append(f"keyTable-utf8[:{sz}]")
        except Exception:
            pass

    # ── 2. deviceNo padded to 16 bytes ────────────────────────────────────────
    if device_no:
        dn_bytes = device_no.strip().encode("utf-8")
        for sz in (16, 24, 32):
            result = _try_decrypt(f"deviceNo-pad{sz}", (dn_bytes + b"\x00" * sz)[:sz])
            if result:
                return result
            tried.append(f"deviceNo-pad{sz}")

    # ── 3. taxpayerID alone (no date) padded to 16 bytes ─────────────────────
    for prefix, lbl in [(taxpayer_id, "taxpayer_id"), (tin or "", "tin")]:
        if not prefix:
            continue
        pfx_bytes = prefix.encode("utf-8")
        for sz in (16, 24, 32):
            result = _try_decrypt(f"{lbl}-alone-pad{sz}", (pfx_bytes + b"\x00" * sz)[:sz])
            if result:
                return result
            tried.append(f"{lbl}-alone-pad{sz}")

    # ── 4. TaxpayerID / TIN + date (SAP B1 EncryptionHelper.cs) ──────────────
    def _make_key(prefix: str, date_str: str) -> bytes:
        combined = (prefix + date_str).encode("utf-8")
        return combined[:16].ljust(16, b"\x00")

    id_prefix  = (taxpayer_id or "")[:10]
    tin_prefix = (tin or "")[:10]

    for fmt in ("%d%m%y", "%d%m%Y", "%Y%m%d", "%m%d%y"):
        date_str = today.strftime(fmt)
        for prefix, label_prefix in [(id_prefix, "taxpayer_id"), (tin_prefix, "tin")]:
            if not prefix:
                continue
            result = _try_decrypt(f"{label_prefix}+{fmt}", _make_key(prefix, date_str))
            if result:
                return result
            tried.append(f"{label_prefix}+{fmt}")

    # ── 5. clientPriKey is not encrypted — raw PEM or DER ────────────────────
    # 5a. Base64-decoded bytes are the raw PEM text
    try:
        text = raw.decode("utf-8").strip()
        if _looks_like_private_key(text):
            _frappe.logger("efris").info("T102: clientPriKey is unencrypted PEM ✓")
            return text
    except Exception:
        pass
    tried.append("raw-utf8-pem")

    # 5b. raw bytes are DER → import as RSA key → export to PEM
    try:
        from Crypto.PublicKey import RSA as _RSA
        key = _RSA.import_key(raw)
        pem = key.export_key("PEM").decode("utf-8")
        _frappe.logger("efris").info("T102: clientPriKey is unencrypted DER ✓")
        return pem
    except Exception:
        pass
    tried.append("raw-DER-import")

    # 5c. Double base64 — base64(base64(PEM))
    try:
        inner = base64.b64decode(raw.decode("utf-8").strip() + "==")
        text  = inner.decode("utf-8").strip()
        if _looks_like_private_key(text):
            _frappe.logger("efris").info("T102: clientPriKey is double-base64 PEM ✓")
            return text
    except Exception:
        pass
    tried.append("double-base64-pem")

    # ── Nothing worked — log diagnostics (committed separately) and raise ─────
    try:
        import frappe as _f
        _f.log_error(
            f"T102 clientPriKey decryption exhausted all {len(tried)} strategies.\n"
            f"taxpayer_id  = '{taxpayer_id}'  (prefix: '{id_prefix}')\n"
            f"tin          = '{tin}'          (prefix: '{tin_prefix}')\n"
            f"device_no    = '{device_no}'\n"
            f"otp          = '{otp}'\n"
            f"keyTable     = '{(key_table or '')[:80]}'\n"
            f"today        = {today.strftime('%Y-%m-%d')}\n"
            f"raw length   = {len(raw)} bytes\n"
            f"raw[:64] hex = {raw[:64].hex()}\n"
            f"approaches tried: {tried}",
            "EFRIS T102 Decrypt Failed",
        )
        _f.db.commit()   # commit log even if caller rolls back
    except Exception:
        pass

    raise ValueError(
        f"Could not decrypt the private key returned by EFRIS (tried {len(tried)} methods).<br><br>"
        f"<b>keyTable</b>: {'✅ present' if key_table else '❌ missing'}<br>"
        f"<b>OTP sent</b>: <code>{otp or '(none)'}</code><br>"
        f"<b>taxpayer_id</b>: <code>{taxpayer_id}</code><br><br>"
        "See <b>Error Log → EFRIS T102 Decrypt Failed</b> for the raw hex bytes.<br>"
        "Share that log entry to identify the correct decryption approach."
    )


def _looks_like_private_key(text: str) -> bool:
    """Heuristic: does this look like a decrypted RSA private key?"""
    if not text or len(text) < 50:
        return False
    # PEM format
    if "-----BEGIN" in text or "PRIVATE KEY" in text:
        return True
    # Raw base64 key (no header) — long, looks like base64
    b64_chars = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=\n\r ")
    if len(text) > 200 and all(c in b64_chars for c in text):
        return True
    return False
