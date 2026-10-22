"""
Key Manager — reads and writes EFRIS cryptographic keys stored in the database.

Keys are stored directly in EFRIS Settings fields:
  private_key      — RSA private key (base64 PKCS#8 or PEM) — Code field
  public_key       — EFRIS server RSA public key (base64 or PEM) — Code field
  session_aes_key  — Session AES key (base64, 16/24/32 bytes) — Data field

No file paths are used. All key content lives in the database, editable
from the EFRIS Settings form. T102 auto-fills private_key and public_key;
T104 auto-fills session_aes_key via db_set().

To paste keys manually:
  - private_key   : paste content of PrivateKey.txt   (base64 PKCS#8 or PEM)
  - public_key    : paste content of PublicKey.txt     (base64 or PEM)
  - session_aes_key: paste content of SessionAESKey.txt (base64, e.g. x/SVvBh4S49ddPNX+nXQYg==)
"""

import base64

import frappe


class KeyManager:
    def __init__(self, settings):
        self.s = settings

    # ─── Server RSA Public Key ─────────────────────────────────────────────

    def get_public_key(self) -> str:
        """Return the EFRIS server RSA public key from the database field."""
        key = (self.s.get("public_key") or "").strip()
        if not key:
            raise RuntimeError(
                "EFRIS Server Public Key is not configured in EFRIS Settings. "
                "Run Full Initialise to fill it, or paste PublicKey.txt content manually."
            )
        return key

    def save_public_key(self, pem: str):
        """Persist server public key to the database field."""
        val = pem.strip()
        self.s.db_set("public_key", val, update_modified=False)
        self.s.public_key = val   # update in-memory too

    # ─── Client RSA Private Key ────────────────────────────────────────────

    def get_private_key(self) -> str:
        """
        Return private key content from the database field.

        Supports:
          • PEM format     (-----BEGIN PRIVATE KEY-----)
          • Raw base64     PKCS#8 without headers (SAP B1 CNG export format)

        _import_rsa_key() in encryption.py handles both formats, so we
        just return the raw field content — no conversion needed here.
        """
        key = (self.s.get("private_key") or "").strip()
        if not key:
            raise RuntimeError(
                "Private Key is not configured in EFRIS Settings. "
                "Run Full Initialise to fill it, or paste PrivateKey.txt content manually. "
                "Accepted formats: base64 PKCS#8 (SAP B1 export) or PEM."
            )
        return key

    def save_private_key(self, pem: str):
        """Persist client private key to the database field."""
        val = pem.strip()
        self.s.db_set("private_key", val, update_modified=False)
        self.s.private_key = val   # update in-memory too

    # ─── Session AES Key ──────────────────────────────────────────────────

    def get_aes_key(self) -> bytes:
        """
        Read the session AES key from the session_aes_key database field.

        The field stores the key as base64 (e.g. x/SVvBh4S49ddPNX+nXQYg==).
        Raises RuntimeError (not frappe.throw) if unavailable so the caller
        (EFRISClient._get_aes_key) can trigger auto-initialisation silently.
        """
        key_b64 = (self.s.get("session_aes_key") or "").strip()
        if not key_b64:
            raise RuntimeError(
                "Session AES Key is not configured in EFRIS Settings. "
                "Click 'Full Initialise' to fill it, "
                "or paste SessionAESKey.txt content manually."
            )
        key = _parse_aes_key_b64(key_b64)
        if not key:
            raise RuntimeError(
                f"Cannot parse AES key from session_aes_key field. "
                f"Expected a base64-encoded 16, 24, or 32-byte key. "
                f"Value preview: {key_b64[:40]}"
            )
        return key

    def save_aes_key(self, key_bytes: bytes):
        """Persist AES session key (raw bytes) as base64 to the session_aes_key field."""
        b64_val = base64.b64encode(key_bytes).decode("ascii")
        self.s.db_set("session_aes_key", b64_val, update_modified=False)
        self.s.session_aes_key = b64_val   # update in-memory too


# ─── AES key parsing helpers ──────────────────────────────────────────────────

def _parse_aes_key_b64(text: str) -> "bytes | None":
    """
    Parse a base64 (or hex) string into AES key bytes.
    Accepts standard base64, URL-safe base64, with or without padding.
    Valid AES key sizes: 16 (AES-128), 24 (AES-192), 32 (AES-256) bytes.
    """
    text = text.strip()

    # 1. Standard / URL-safe base64 (with padding normalisation)
    for variant in (text, text.replace("-", "+").replace("_", "/")):
        try:
            key = base64.b64decode(variant + "=" * (-len(variant) % 4))
            if len(key) in (16, 24, 32):
                return key
        except Exception:
            pass

    # 2. Hex string  (32/48/64 hex chars = 16/24/32 bytes)
    if len(text) in (32, 48, 64):
        try:
            key = bytes.fromhex(text)
            if len(key) in (16, 24, 32):
                return key
        except ValueError:
            pass

    return None


def _parse_aes_key(raw: bytes) -> "bytes | None":
    """
    Legacy helper: parse raw bytes (binary, hex text, or base64 text) into AES key.
    Kept for backwards-compatibility with any code that calls this directly.
    """
    # 1. Raw binary
    if len(raw) in (16, 24, 32):
        return raw

    try:
        text = raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None

    return _parse_aes_key_b64(text)
