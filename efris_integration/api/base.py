"""
Base EFRIS API handler.

Handles the universal request/response envelope, encryption, and HTTP transport.
Mirrors: ClsDIGeneric.cs  +  Request.cs  +  Response.cs

Key architecture:
  - All T-codes POST to the SAME endpoint (api_url = getInformation)
  - The interface code inside globalInfo routes the call server-side
  - Data is AES-128-ECB encrypted, optionally GZip compressed, Base64 encoded
  - Keys are managed by KeyManager (file-path-first, DB fallback)
"""

import json

import frappe
import requests

from efris_integration.utils.encryption import decode_response_data, encode_request_data
from efris_integration.utils.helpers import log_efris, make_global_info


class EFRISAPIError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"EFRIS Error [{code}]: {message}")


class EFRISClient:
    """
    Stateless EFRIS API client.
    Instantiate once per logical operation; do not cache across requests.
    """

    def __init__(self):
        self.settings = frappe.get_single("EFRIS Settings")
        self._km = None         # KeyManager — lazy-loaded
        self._aes_key = None    # cached bytes for this request lifecycle

    # ──────────────────────────────────────────────────────────────────────
    # Primary public API
    # ──────────────────────────────────────────────────────────────────────

    def call(
        self,
        interface_code: str,
        payload: dict | None = None,
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
            raise EFRISAPIError(return_code, return_msg)

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

                # Content not a multiple of 16 bytes → not actually AES-ECB.
                # EFRIS sometimes marks unencrypted responses as encryptCode=2.
                # Fall through to plain-content parser instead of crashing.
                if "block boundary" in err or "aligned" in err or "multiple" in err:
                    frappe.logger("efris").warning(
                        f"[{interface_code}] encryptCode=2 but content is not "
                        f"AES-aligned — falling back to plain parse. ({exc})"
                    )
                    return _parse_plain_content(content)

                # Wrong padding → stale session key — raise SESSION_EXPIRED
                # so the caller (exchange_rate._fetch_rates) can auto-refresh.
                if "padding" in err or "incorrect" in err:
                    raise EFRISAPIError("SESSION_EXPIRED", "Session AES key is stale.")

                frappe.throw(f"EFRIS response decryption failed: {exc}")

        # Unencrypted content — try every known format
        return _parse_plain_content(content)

    # ──────────────────────────────────────────────────────────────────────
    # Key access helpers (delegates to KeyManager)
    # ──────────────────────────────────────────────────────────────────────

    def _get_aes_key(self) -> bytes:
        """
        Return the session AES key, auto-initialising if necessary.

        SAP B1 never prompts the user to initialise — it just works.
        If the session key file is missing we silently run T103+T104.
        If the private key file is also missing we run T102 first.
        """
        if self._aes_key is not None:
            return self._aes_key

        try:
            self._aes_key = self.key_manager.get_aes_key()
            return self._aes_key
        except Exception:
            pass  # key file missing or unreadable — auto-init below

        frappe.logger("efris").info(
            "EFRIS: session AES key not found — auto-initialising (T103+T104)."
        )
        try:
            self._auto_init_session()
        except Exception as exc:
            frappe.log_error(frappe.get_traceback(), "EFRIS Auto-Init Failed")
            frappe.throw(
                f"EFRIS session could not be initialised automatically: {exc}<br>"
                "Check <b>Error Log → EFRIS Auto-Init Failed</b> for details.<br>"
                "Ensure EFRIS Settings (API URL, TaxpayerID, key file paths) are correct."
            )
        self._aes_key = self.key_manager.get_aes_key()
        return self._aes_key

    def _auto_init_session(self):
        """
        Silently run T103 + T104 to obtain a fresh session AES key.

        Official URA online mode order: T101 → T104 → T103.
        T102 is NOT part of the direct/online flow — the private key is
        generated by the taxpayer and registered with EFRIS independently.

        Both T103 and T104 are run here so the session is fully valid.
        Any exception (e.g. private key not configured) is allowed to
        propagate so the user sees the real root cause.
        """
        from efris_integration.api.auth import sign_in, get_symmetric_key

        # URA guide order: T104 first (get AES key), then T103 (sign in)
        get_symmetric_key() # T104: RSA-decrypt passowrdDes → session AES key
        sign_in()           # T103: sign in / get device info
        self._aes_key = None  # force reload from file after T104 writes it

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
