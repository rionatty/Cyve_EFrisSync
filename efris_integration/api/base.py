"""
Base EFRIS API handler — per-company.

Handles the universal request/response envelope, encryption, and HTTP transport.
Mirrors: ClsDIGeneric.cs  +  Request.cs  +  Response.cs

Key architecture:
  - EFRISClient is instantiated with a company name; settings are loaded per company.
  - All T-codes POST to the SAME endpoint (api_url = getInformation)
  - The interface code inside globalInfo routes the call server-side
  - Data is AES-128-ECB encrypted, optionally GZip compressed, Base64 encoded
  - Keys are managed by KeyManager (stored in EFRIS Settings DB fields)
"""

import json

import frappe
import requests

from efris_integration.utils.encryption import decode_response_data, encode_request_data
from efris_integration.utils.helpers import get_efris_settings, log_efris, make_global_info


class EFRISAPIError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"EFRIS Error [{code}]: {message}")


class EFRISClient:
    """
    Stateless EFRIS API client — one instance per company per logical operation.
    Do not cache across requests.
    """

    def __init__(self, company: str):
        if not company:
            frappe.throw("EFRISClient requires a company name.", title="EFRIS Config Error")
        self.company  = company
        self.settings = get_efris_settings(company)
        self._km      = None      # KeyManager — lazy-loaded
        self._aes_key = None      # cached bytes for this request lifecycle

    # ──────────────────────────────────────────────────────────────────────
    # Primary public API
    # ──────────────────────────────────────────────────────────────────────

    def call(
        self,
        interface_code: str,
        payload: "dict | list | None" = None,
        *,
        doc_type: str = "",
        doc_name: str = "",
        encrypt: bool = True,
        sign: bool = False,
    ) -> dict:
        """
        Build → encrypt → (optionally sign) → POST → decrypt → return inner data dict.
        Raises EFRISAPIError when EFRIS returnCode != '00'.

        sign=True: adds RSA SHA1 PKCS1v1.5 signature of the base64 content string.
                   Mirrors ExchangeRateForm.cs: RSACng.SignData(UTF8(contentB64), SHA1, PKCS1)
                   Required for T126 (exchange rate) and any T-code that needs a signature field.
        """
        global_info = make_global_info(interface_code, self.settings)

        # ── Build data block ───────────────────────────────────────────
        if payload and encrypt:
            data_block = encode_request_data(payload, self._get_aes_key(), compress=False)
            if sign:
                from efris_integration.utils.encryption import rsa_sign
                data_block["signature"] = rsa_sign(
                    data_block["content"],
                    self.key_manager.get_private_key(),
                )
        elif payload:
            # Auth calls (T101–T104) sent plain (encryptCode=0)
            data_block = {
                "content": json.dumps(payload, separators=(",", ":")),
                "dataDescription": {"zipCode": "0", "encryptCode": "0"},
            }
        else:
            data_block = {
                "content": "",
                "dataDescription": {"zipCode": "0", "encryptCode": "0"},
            }

        request_body = {
            "globalInfo": global_info,
            "data": data_block,
            "returnStateInfo": {"returnCode": "00", "returnMessage": "Success"},
        }

        # ── Send ───────────────────────────────────────────────────────
        response = self._http_post(request_body)
        self.last_envelope = response   # globalInfo.extendField.referenceNo (T131)

        return_code = response.get("returnStateInfo", {}).get("returnCode", "")
        return_msg  = response.get("returnStateInfo", {}).get("returnMessage", "")

        # ── Log ────────────────────────────────────────────────────────
        log_efris(
            doc_type, doc_name, interface_code,
            request_body, response,
            "Success" if return_code == "00" else "Failed",
            "" if return_code == "00" else return_msg,
        )

        if return_code != "00":
            # For "partial failure" (45) and similar batch codes, EFRIS puts the
            # per-item field errors inside the encrypted response body.
            # Decrypt it here so callers get actionable detail, not just "Partial failure!".
            detail = _extract_error_detail(response.get("data", {}), self._get_aes_key)
            raise EFRISAPIError(return_code, return_msg + (f" | {detail}" if detail else ""))

        # ── Decrypt response ───────────────────────────────────────────
        resp_data = response.get("data", {})
        content = resp_data.get("content", "")

        if not content:
            return {}

        desc = resp_data.get("dataDescription", {})
        encrypt_code = str(desc.get("encryptCode", "0"))

        if encrypt_code == "2":
            try:
                return decode_response_data(resp_data, self._get_aes_key())
            except (ValueError, Exception) as exc:
                err = str(exc).lower()

                # Always log the raw content so we can debug key issues
                frappe.log_error(
                    f"[{interface_code}] AES decrypt failed: {exc}\n"
                    f"content length : {len(content)} chars\n"
                    f"content preview: {content[:120]}\n"
                    f"encryptCode    : {encrypt_code}\n"
                    f"zipCode        : {desc.get('zipCode', '?')}\n"
                    f"company        : {self.company}",
                    f"EFRIS Decrypt Fail [{interface_code}]",
                )

                # Content not a multiple of 16 bytes → not actually AES-ECB.
                # EFRIS sometimes marks unencrypted responses as encryptCode=2.
                if "block boundary" in err or "aligned" in err or "multiple" in err:
                    return _parse_plain_content(content)

                # Any decryption failure means wrong AES key or unencrypted content.
                # Patterns covered:
                #   "padding" / "incorrect"  — PKCS7 unpad error (wrong key, wrong padding)
                #   "codec" / "utf" / "decode" — garbage bytes from wrong-key AES decrypt
                #   "json"                    — decrypted bytes aren't valid JSON
                #   "unpack"                  — struct unpack failure on garbage
                # Try plain-text parse first in case EFRIS sent unencrypted despite encryptCode=2,
                # then raise SESSION_EXPIRED so the T104 probe loop tries the next candidate.
                _is_decrypt_failure = (
                    "padding" in err or "incorrect" in err
                    or "codec" in err or "utf" in err or "decode" in err
                    or "json" in err or "unpack" in err
                    or "expecting value" in err   # json.loads("") — empty decrypted body
                    or "extra data" in err        # json.loads of binary garbage
                    or isinstance(exc, (ValueError, UnicodeDecodeError))
                )
                if _is_decrypt_failure:
                    plain = _parse_plain_content(content)
                    if plain:
                        return plain
                    raise EFRISAPIError("SESSION_EXPIRED", "Session AES key is stale — wrong key.")

                frappe.throw(f"EFRIS response decryption failed: {exc}")

        # Unencrypted content — try every known format
        return _parse_plain_content(content)

    # ──────────────────────────────────────────────────────────────────────
    # Key access helpers (delegates to KeyManager)
    # ──────────────────────────────────────────────────────────────────────

    def _get_aes_key(self) -> bytes:
        """
        Return the session AES key.

        If the Session AES Key field in EFRIS Settings is empty → throw immediately
        with a clear instruction to paste it. This matches how SAP B1 works:
        it reads the key directly from SessionAESKey.txt and uses it; no auto-init.

        Auto-init (T103+T104) is only attempted when the field exists but the key
        appears to be expired (SESSION_EXPIRED from a live call) — not on every boot.
        """
        if self._aes_key is not None:
            return self._aes_key

        try:
            self._aes_key = self.key_manager.get_aes_key()
            return self._aes_key
        except RuntimeError as exc:
            msg = str(exc)
            # Field is empty — give a direct, actionable error. Do NOT auto-init.
            # (SAP B1 would also fail here if SessionAESKey.txt were missing.)
            if "not configured" in msg or "is not configured" in msg:
                frappe.throw(
                    f"<b>Session AES Key</b> is not set in EFRIS Settings for "
                    f"company <b>{self.company}</b>.<br><br>"
                    "Open <b>EFRIS Settings</b> → <b>Cryptographic Keys</b> and paste the "
                    "content of <b>SessionAESKey.txt</b> into the <b>Session AES Key</b> field, "
                    "then save.<br><br>"
                    f"Your key: <code>x/SVvBh4S49ddPNX+nXQYg==</code>",
                    title="Session AES Key Not Configured",
                )
            # Field has a value but it can't be parsed (wrong format) → same instruction
            if "cannot parse" in msg or "Cannot parse" in msg:
                frappe.throw(
                    f"<b>Session AES Key</b> in EFRIS Settings for <b>{self.company}</b> "
                    f"is not a valid base64 AES key.<br><br>"
                    f"It must be exactly 16, 24, or 32 bytes when base64-decoded.<br>"
                    f"Error: {msg}",
                    title="Session AES Key Invalid",
                )
            # Other runtime error — fall through to auto-init attempt
        except Exception:
            pass  # unexpected error — try auto-init as last resort

        # Key exists but seems stale or parse issue — try auto-refresh once
        frappe.logger("efris").info(
            f"EFRIS [{self.company}]: session AES key issue — attempting T103+T104 refresh."
        )
        try:
            self._auto_init_session()
        except Exception as exc:
            frappe.log_error(frappe.get_traceback(), f"EFRIS Auto-Init Failed [{self.company}]")
            frappe.throw(
                f"EFRIS session for <b>{self.company}</b> could not be auto-refreshed: {exc}<br>"
                "Paste your <b>SessionAESKey.txt</b> content directly into the "
                "<b>Session AES Key</b> field in EFRIS Settings.",
                title="EFRIS Session Error",
            )
        self._aes_key = self.key_manager.get_aes_key()
        return self._aes_key

    def _auto_init_session(self):
        """
        Silently run T103 → T104 to obtain a fresh session AES key.

        CRITICAL: T103 must run BEFORE T104.
        Calling T103 after T104 resets the EFRIS session on the server,
        invalidating the AES key that was just saved. Running T103 first
        settles the session, then T104 fetches a key that stays valid.
        """
        from efris_integration.api.auth import sign_in, get_symmetric_key

        try:
            sign_in(self.company)   # T103: settle/initialise the session
        except Exception:
            pass                    # T103 failure is non-fatal — T104 may still work

        get_symmetric_key(self.company)   # T104: get fresh session AES key
        self._aes_key = None              # force reload from file

    def set_aes_key(self, key_bytes: bytes):
        """Called by auth.py after T104 to persist the session AES key."""
        self._aes_key = key_bytes
        self.key_manager.save_aes_key(key_bytes)

    @property
    def key_manager(self):
        if self._km is None:
            from efris_integration.api.key_manager import KeyManager
            self._km = KeyManager(self.settings)
        return self._km

    # ──────────────────────────────────────────────────────────────────────
    # HTTP transport
    # ──────────────────────────────────────────────────────────────────────

    def _http_post(self, body: dict) -> dict:
        """
        POST the envelope to the EFRIS endpoint.
        The URL is the complete endpoint, e.g.:
            https://efristest.ura.go.ug/efrisws/ws/taapp/getInformation
        All T-codes share this same endpoint; routing is done by interfaceCode.
        """
        url = self.settings.api_url.rstrip("/")
        try:
            resp = requests.post(
                url,
                json=body,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()

        except requests.exceptions.Timeout:
            frappe.throw(
                f"EFRIS API timed out after 30 s. "
                f"Check connectivity to: {url}"
            )
        except requests.exceptions.ConnectionError as exc:
            frappe.throw(
                f"Cannot reach EFRIS endpoint: {url}\n{exc}"
            )
        except requests.exceptions.HTTPError as exc:
            frappe.throw(
                f"EFRIS HTTP {exc.response.status_code}: {exc.response.text[:300]}"
            )
        except (json.JSONDecodeError, ValueError) as exc:
            frappe.throw(
                f"EFRIS returned invalid JSON. Raw: {exc}"
            )


def _extract_error_detail(resp_data: dict, get_key_fn) -> str:
    """
    For T130/T131 "partial failure" (code 45), EFRIS returns per-item errors
    inside the encrypted response body.  Decrypt it and build a readable summary.

    Returns an empty string if decryption fails or no per-item errors are found.
    """
    try:
        from efris_integration.utils.encryption import decode_response_data as _decode

        content = resp_data.get("content", "")
        if not content:
            return ""

        desc = resp_data.get("dataDescription", {})
        encrypt_code = str(desc.get("encryptCode", "0"))

        if encrypt_code == "2":
            items = _decode(resp_data, get_key_fn())
        else:
            items = _parse_plain_content(content)

        # Normalise to list
        if isinstance(items, dict):
            items = items.get("data", [items])
        if not isinstance(items, list):
            return ""

        msgs = []
        for item in items:
            if not isinstance(item, dict):
                continue
            rc  = str(item.get("returnCode") or item.get("returncode") or "")
            rm  = str(item.get("returnMessage") or item.get("returnmessage") or "")
            gc  = str(item.get("goodsCode") or item.get("goodscode") or "")
            if rc and rc != "00":
                label = f"{gc}: " if gc else ""
                msgs.append(f"{label}[{rc}] {rm}")

        return " | ".join(msgs) if msgs else ""

    except Exception:
        return ""


def _parse_plain_content(content) -> dict:
    """
    Parse the `data.content` field from an unencrypted EFRIS response.

    EFRIS can return content in four forms depending on T-code and version:
      1. Python dict already parsed by requests.json()
      2. Plain JSON string  e.g. '{"serverPublicKey":"..."}'
      3. Base64-encoded JSON string  (most common for T101/T102/T103/T104)
      4. Base64-encoded JSON wrapped in a list  e.g. '[{...}]'
    """
    import base64

    # 1. Already a dict or list
    if isinstance(content, (dict, list)):
        return content if isinstance(content, dict) else {"data": content}

    if not isinstance(content, str) or not content.strip():
        return {}

    text = content.strip()

    # 2. Plain JSON object or array
    if text.startswith(("{", "[")):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return {"data": parsed}
            return parsed
        except json.JSONDecodeError:
            pass

    # 3. Base64-encoded JSON (most EFRIS T10x responses)
    try:
        decoded = base64.b64decode(text + "==")   # pad liberally
        decoded_str = decoded.decode("utf-8").strip()
        if decoded_str.startswith(("{", "[")):
            parsed = json.loads(decoded_str)
            if isinstance(parsed, list):
                return {"data": parsed}
            return parsed
    except Exception:
        pass

    # 4. Nothing worked — log and return empty so caller can report the gap
    frappe.log_error(
        f"_parse_plain_content: unrecognised content format: {text[:200]}",
        "EFRIS Response Parse Warning",
    )
    return {}
