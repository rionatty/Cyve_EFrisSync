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


def rsa_decrypt(encrypted_b64: str, private_key_pem: str) -> bytes:
    key = RSA.import_key(private_key_pem)
    cipher = PKCS1_OAEP.new(key)
    return cipher.decrypt(base64.b64decode(encrypted_b64))


def rsa_encrypt(data: bytes, public_key_pem: str) -> str:
    key = RSA.import_key(public_key_pem)
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

    return json.loads(raw.decode("utf-8"))


def rsa_sign(content_b64: str, private_key_pem: str) -> str:
    """
    RSA SHA1 PKCS1v1.5 signature of the base64-encoded content string.

    Mirrors ExchangeRateForm.cs exactly:
        CngKey.Import(privateKey, Pkcs8PrivateBlob)
        RSACng.SignData(UTF8.GetBytes(contentB64), SHA1, PKCS1)

    Args:
        content_b64: the base64 string of the AES-encrypted payload
        private_key_pem: client private key PEM (from PrivateKey.txt)
    Returns:
        base64-encoded signature string
    """
    key = RSA.import_key(private_key_pem)
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


def try_decrypt_private_key(encrypted_b64: str, taxpayer_id: str, tin: str = "") -> str:
    """
    Robustly decrypt the T102 clientPriKey by trying every plausible key
    derivation and padding approach.

    EFRIS encrypts the private key with AES-128-ECB where the key is derived
    from (TaxpayerID[:10] + date).  Different EFRIS server versions, test vs
    production, and URA API revisions have used slightly different formats,
    so we try them all and return the first result that looks like a private key.

    Attempts (in order):
      1. taxpayer_id[:10] + ddMMyy    ← SAP B1 EncryptionHelper.cs (standard)
      2. taxpayer_id[:10] + ddMMyyyy  ← 4-digit year variant
      3. taxpayer_id[:10] + yyyyMMdd  ← ISO date variant
      4. taxpayer_id[:10] + MMddyy    ← US-style date
      5. tin[:10]         + ddMMyy    ← Some servers use TIN instead
      6. tin[:10]         + ddMMyyyy
      7. Zero-strip (no PKCS7) for each key above
      8. Not encrypted — raw base64-decoded PEM (test environment)
      9. Plain base64 of "-----BEGIN..." PEM string

    Raises ValueError if all attempts fail (includes diagnostic info in Error Log).
    """
    from datetime import datetime
    from Crypto.Cipher import AES as _AES
    from Crypto.Util.Padding import unpad as _unpad

    raw = base64.b64decode(encrypted_b64 + "==")
    today = datetime.now()

    # ── Build candidate keys ─────────────────────────────────────────────────
    candidates: list[tuple[str, bytes]] = []

    def _make_key(prefix: str, date_str: str) -> bytes:
        combined = (prefix + date_str).encode("utf-8")
        return combined[:16].ljust(16, b"\x00")

    id_prefix  = taxpayer_id[:10]
    tin_prefix = (tin or "")[:10]

    for fmt in ("%d%m%y", "%d%m%Y", "%Y%m%d", "%m%d%y"):
        date_str = today.strftime(fmt)
        candidates.append((f"taxpayer_id+{fmt}", _make_key(id_prefix, date_str)))
        if tin_prefix:
            candidates.append((f"tin+{fmt}", _make_key(tin_prefix, date_str)))

    # ── Try each candidate with PKCS7, then zero-strip ───────────────────────
    tried = []
    for label, aes_key in candidates:
        cipher = _AES.new(aes_key, _AES.MODE_ECB)
        decrypted = cipher.decrypt(raw)

        # PKCS7
        try:
            plain = _unpad(decrypted, _AES.block_size, style="pkcs7")
            text  = plain.decode("utf-8").strip()
            if _looks_like_private_key(text):
                import frappe as _frappe
                _frappe.logger("efris").info(
                    f"T102: clientPriKey decrypted OK — approach: {label}"
                )
                return text
        except Exception:
            pass

        # Zero / null strip (some implementations use NUL-byte padding)
        try:
            plain = decrypted.rstrip(b"\x00")
            text  = plain.decode("utf-8").strip()
            if _looks_like_private_key(text):
                import frappe as _frappe
                _frappe.logger("efris").info(
                    f"T102: clientPriKey decrypted (zero-pad) — approach: {label}"
                )
                return text
        except Exception:
            pass

        tried.append(label)

    # ── Not encrypted? Test environment returns raw PEM base64 ───────────────
    try:
        text = raw.decode("utf-8").strip()
        if _looks_like_private_key(text):
            return text
    except Exception:
        pass

    # ── Nothing worked — log diagnostics ─────────────────────────────────────
    try:
        import frappe as _frappe
        _frappe.log_error(
            f"T102 clientPriKey decryption exhausted all strategies.\n"
            f"taxpayer_id = '{taxpayer_id}'  (prefix used: '{id_prefix}')\n"
            f"tin         = '{tin}'          (prefix used: '{tin_prefix}')\n"
            f"today       = {today.strftime('%Y-%m-%d')}\n"
            f"raw length  = {len(raw)} bytes\n"
            f"raw[:32]    = {raw[:32].hex()}\n"
            f"approaches tried: {tried}",
            "EFRIS T102 Decrypt Failed",
        )
    except Exception:
        pass

    raise ValueError(
        "T102 clientPriKey: AES decryption failed with every known key "
        f"derivation (tried {len(tried)} approaches).<br><br>"
        f"<b>taxpayer_id</b>: <code>{taxpayer_id}</code><br>"
        "<b>Check</b>: EFRIS Settings → Company Info — ensure TaxpayerID "
        "matches the 10-character EFRIS device registration ID exactly.<br>"
        "See <b>Error Log → EFRIS T102 Decrypt Failed</b> for raw diagnostic bytes."
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
