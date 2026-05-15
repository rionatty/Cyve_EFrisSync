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
    ) -> dict:
        """
        Build → encrypt → POST → decrypt → return inner data dict.
        Raises EFRISAPIError when EFRIS returnCode != '00'.
        """
        global_info = make_global_info(interface_code, self.settings)

        # ── Build data block ───────────────────────────────────────────
        if payload and encrypt:
            data_block = encode_request_data(payload, self._get_aes_key(), compress=False)
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
            return decode_response_data(resp_data, self._get_aes_key())

        # Unencrypted / plain JSON string in content
        if isinstance(content, str) and content.strip().startswith("{"):
            return json.loads(content)
        if isinstance(content, dict):
            return content
        return {}

    # ──────────────────────────────────────────────────────────────────────
    # Key access helpers (delegates to KeyManager)
    # ──────────────────────────────────────────────────────────────────────

    def _get_aes_key(self) -> bytes:
        if self._aes_key is None:
            self._aes_key = self.key_manager.get_aes_key()
        return self._aes_key

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
