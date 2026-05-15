"""
Key Manager — reads and writes EFRIS cryptographic keys.

Priority order (mirrors exactly how SAP B1 handles key files):
  1. File path configured in EFRIS Settings → read/write the actual .txt file
  2. Database (EFRIS Settings password field) → fallback when no path given

SAP B1 key files:
  PublicKey.txt    → server RSA public key (PEM)
  PrivateKey.txt   → client RSA private key (PEM), AES-encrypted at init, then plain
  SessionAESKey.txt → session AES key (raw binary or base64)

In ERPNext we store:
  - public_key_path / server_public_key DB field
  - private_key_path / client_private_key DB field
  - session_key_path / session_aes_key DB field (base64-encoded bytes)
"""

import base64
import os

import frappe


class KeyManager:
    def __init__(self, settings):
        self.s = settings

    # ─── Public Key (server RSA) ───────────────────────────────────────────

    def get_public_key(self) -> str:
        """Return server RSA public key as PEM string."""
        return self._read_text("public_key_path", "server_public_key")

    def save_public_key(self, pem: str):
        self._write_text("public_key_path", "server_public_key", pem)

    # ─── Private Key (client RSA) ──────────────────────────────────────────

    def get_private_key(self) -> str:
        """Return client RSA private key as PEM string."""
        return self._read_text("private_key_path", "client_private_key", password=True)

    def save_private_key(self, pem: str):
        self._write_text("private_key_path", "client_private_key", pem, password=True)

    # ─── Session AES Key ───────────────────────────────────────────────────

    def get_aes_key(self) -> bytes:
        """
        Return session AES key as raw bytes.

        Handles all formats written by SAP B1 or this app:
          1. Raw 16/24/32 binary bytes
          2. Base64-encoded string (with or without newlines/padding)
          3. Hex-encoded string (32 hex chars = 16 bytes)
        """
        path = self.s.get("session_key_path")
        if path and os.path.isfile(path):
            raw = _read_file_bytes(path)
            key = _parse_aes_key_bytes(raw)
            if key:
                return key
            frappe.throw(
                f"Cannot parse AES key from file '{path}'. "
                "Expected 16/24/32 raw bytes, base64, or hex string. "
                "Re-run Full Initialise (T101→T104) to regenerate it."
            )

        # Fallback: DB password field
        raw_b64 = self.s.get_password("session_aes_key", raise_exception=False)
        if not raw_b64:
            frappe.throw(
                "Session AES key not found in file or database. "
                "Run Full Initialise (T101→T104) from EFRIS Settings first."
            )
        # DB value is always stored as base64 by save_aes_key()
        try:
            return base64.b64decode(raw_b64)
        except Exception:
            frappe.throw("Session AES key in database is corrupted. Re-run Full Initialise.")

    def save_aes_key(self, key_bytes: bytes):
        """Persist AES key to file (if path configured) AND to DB."""
        path = self.s.get("session_key_path")
        if path:
            _ensure_dir(path)
            _write_file_bytes(path, key_bytes)

        # Always also store in DB as base64 (belt-and-suspenders)
        self.s.db_set(
            "session_aes_key",
            base64.b64encode(key_bytes).decode(),
            update_modified=False,
        )

    # ─── Internal helpers ──────────────────────────────────────────────────

    def _read_text(self, path_field: str, db_field: str, password: bool = False) -> str:
        path = self.s.get(path_field)
        if path and os.path.isfile(path):
            return _read_file_text(path)
        # Fallback: DB
        if password:
            val = self.s.get_password(db_field, raise_exception=False)
        else:
            val = self.s.get(db_field)
        if not val:
            frappe.throw(
                f"Key not found in file ({path or 'no path set'}) or database field '{db_field}'. "
                "Run Full Initialise first."
            )
        return val

    def _write_text(self, path_field: str, db_field: str, content: str, password: bool = False):
        path = self.s.get(path_field)
        if path:
            _ensure_dir(path)
            _write_file_text(path, content)

        # Always also persist to DB
        self.s.db_set(db_field, content, update_modified=False)


# ─── Low-level file I/O ────────────────────────────────────────────────────

def _read_file_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError as e:
        frappe.throw(f"Cannot read key file '{path}': {e}")


def _read_file_bytes(path: str) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError as e:
        frappe.throw(f"Cannot read key file '{path}': {e}")


def _write_file_text(path: str, content: str):
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as e:
        frappe.throw(f"Cannot write key file '{path}': {e}")


def _write_file_bytes(path: str, content: bytes):
    try:
        with open(path, "wb") as f:
            f.write(content)
    except OSError as e:
        frappe.throw(f"Cannot write key file '{path}': {e}")


def _ensure_dir(path: str):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)


def _parse_aes_key_bytes(raw: bytes) -> bytes | None:
    """
    Try every known encoding to extract a valid AES key (16/24/32 bytes).
    Returns the key bytes on success, or None if nothing works.

    Order of attempts:
      1. Raw binary (file written by save_aes_key in binary mode)
      2. Hex string  (32/48/64 hex chars → 16/24/32 bytes)
      3. Base64 string (standard or URL-safe, with/without padding)
    """
    # 1. Raw binary
    if len(raw) in (16, 24, 32):
        return raw

    # Work with text from here
    try:
        text = raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None

    # 2. Hex string (case-insensitive)
    if len(text) in (32, 48, 64):
        try:
            key = bytes.fromhex(text)
            if len(key) in (16, 24, 32):
                return key
        except ValueError:
            pass

    # 3. Base64 (add padding if needed, try url-safe variant too)
    for variant in (text, text.replace("-", "+").replace("_", "/")):
        padded = variant + "=" * (-len(variant) % 4)
        try:
            key = base64.b64decode(padded)
            if len(key) in (16, 24, 32):
                return key
        except Exception:
            pass

    return None
