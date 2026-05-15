"""
Key Manager — reads and writes EFRIS cryptographic key FILES.

Mirrors exactly how SAP B1 URASetting.cs handles key paths:
  B1 button → U_OT_EPK path → PublicKey.txt
  B2 button → U_OT_SAK path → SessionAESKey.txt
  B3 button → U_OT_PK  path → PrivateKey.txt

Keys are ONLY stored in files. The path fields in EFRIS Settings
(public_key_path, session_key_path, private_key_path) hold the
absolute filesystem paths, exactly as in the SAP @OT_URA table.

No database storage of key content — this matches SAP's approach.
"""

import base64
import os

import frappe


class KeyManager:
    def __init__(self, settings):
        self.s = settings

    # ─── Server RSA Public Key  (U_OT_EPK → PublicKey.txt) ──────────────

    def get_public_key(self) -> str:
        return self._read_key_file("public_key_path", "EFRIS Public Key")

    def save_public_key(self, pem: str):
        self._write_key_file("public_key_path", pem)

    # ─── Client RSA Private Key  (U_OT_PK → PrivateKey.txt) ─────────────

    def get_private_key(self) -> str:
        return self._read_key_file("private_key_path", "Private Key")

    def save_private_key(self, pem: str):
        self._write_key_file("private_key_path", pem)

    # ─── Session AES Key  (U_OT_SAK → SessionAESKey.txt) ────────────────

    def get_aes_key(self) -> bytes:
        """
        Read the session AES key from the file at session_key_path.
        Handles raw binary, hex string, and base64 encodings.
        """
        path = self.s.get("session_key_path")
        if not path:
            frappe.throw(
                "Session Key Path is not configured in EFRIS Settings. "
                "Set the path to SessionAESKey.txt and run Full Initialise."
            )
        if not os.path.isfile(path):
            frappe.throw(
                f"Session key file not found: {path}<br>"
                "Run <b>Full Initialise (T101→T104)</b> to generate it."
            )

        raw = _read_bytes(path)
        key = _parse_aes_key(raw)
        if key:
            return key

        frappe.throw(
            f"Cannot parse AES key from '{path}'.<br>"
            "Expected 16/24/32 raw bytes, hex string, or base64.<br>"
            "Re-run <b>Full Initialise</b> to regenerate the file."
        )

    def save_aes_key(self, key_bytes: bytes):
        """Write session AES key to the file at session_key_path."""
        path = self.s.get("session_key_path")
        if not path:
            frappe.throw(
                "Session Key Path is not configured. "
                "Set it in EFRIS Settings before initialising."
            )
        _ensure_dir(path)
        _write_bytes(path, key_bytes)

    # ─── Internal ──────────────────────────────────────────────────────────

    def _read_key_file(self, path_field: str, label: str) -> str:
        path = self.s.get(path_field)
        if not path:
            frappe.throw(f"{label} path is not configured in EFRIS Settings.")
        if not os.path.isfile(path):
            frappe.throw(
                f"{label} file not found: {path}<br>"
                "Run <b>Full Initialise (T101→T104)</b> to generate it."
            )
        return _read_text(path)

    def _write_key_file(self, path_field: str, content: str):
        path = self.s.get(path_field)
        if not path:
            frappe.throw(
                f"Key path field '{path_field}' is not configured in EFRIS Settings."
            )
        _ensure_dir(path)
        _write_text(path, content)


# ─── Low-level file helpers ────────────────────────────────────────────────────

def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError as e:
        frappe.throw(f"Cannot read key file '{path}': {e}")


def _read_bytes(path: str) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError as e:
        frappe.throw(f"Cannot read key file '{path}': {e}")


def _write_text(path: str, content: str):
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as e:
        frappe.throw(f"Cannot write key file '{path}': {e}")


def _write_bytes(path: str, content: bytes):
    try:
        with open(path, "wb") as f:
            f.write(content)
    except OSError as e:
        frappe.throw(f"Cannot write key file '{path}': {e}")


def _ensure_dir(path: str):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)


def _parse_aes_key(raw: bytes) -> bytes | None:
    """
    Try every encoding to extract a valid AES key (16/24/32 bytes).
    SAP B1 writes raw binary to SessionAESKey.txt; some setups use hex or base64.
    """
    # 1. Raw binary
    if len(raw) in (16, 24, 32):
        return raw

    try:
        text = raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None

    # 2. Hex string (32/48/64 hex chars)
    if len(text) in (32, 48, 64):
        try:
            key = bytes.fromhex(text)
            if len(key) in (16, 24, 32):
                return key
        except ValueError:
            pass

    # 3. Base64 (standard or URL-safe, with optional padding)
    for variant in (text, text.replace("-", "+").replace("_", "/")):
        try:
            key = base64.b64decode(variant + "=" * (-len(variant) % 4))
            if len(key) in (16, 24, 32):
                return key
        except Exception:
            pass

    return None
