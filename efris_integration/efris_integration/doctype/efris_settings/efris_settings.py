# Copyright (c) 2024, CyveTech and contributors
# EFRIS Settings — one document per company

import frappe
from frappe.model.document import Document


class EFRISSettings(Document):

    def validate(self):
        self._validate_required_fields()
        self._clean_keys()
        self._validate_industry_flags()
        self._warn_setup_problems()

    def on_update(self):
        # Invalidate T127 in-memory cache whenever settings change
        try:
            from efris_integration.api.t127 import invalidate_cache
            invalidate_cache()
        except Exception:
            pass

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _validate_required_fields(self):
        required = {
            "api_url":      "URL",
            "device_no":    "Device No",
            "app_id":       "App Id",
            "efris_username": "User Name",
            "tin":          "TIN",
            "taxpayer_id":  "Taxpayer ID",
        }
        for field, label in required.items():
            if not self.get(field):
                frappe.throw(f"<b>{label}</b> is required in EFRIS Settings.")

    def _clean_keys(self):
        """Strip accidental leading/trailing whitespace from key content fields."""
        for field in ("private_key", "public_key", "session_aes_key"):
            val = self.get(field)
            if val:
                self.set(field, val.strip())

    def _warn_setup_problems(self):
        """SAP B1 URASetting checks. Warnings only, so the settings still save."""
        problems = []
        url = (self.get("api_url") or "").strip()
        if url and not url.lower().startswith(("http://", "https://")):
            problems.append("URL should start with https://")
        tin, device = (self.get("tin") or "").strip(), (self.get("device_no") or "").strip()
        if tin and device and not device.startswith(tin):
            problems.append(f"Device No {device} does not start with the TIN {tin}")
        if not (self.get("private_key") or "").strip():
            problems.append("Private Key is empty: URA calls cannot be signed")
        if problems:
            frappe.msgprint("<br>".join(frappe.utils.escape_html(p) for p in problems),
                            title="EFRIS Settings saved with warnings", indicator="orange")

    def _validate_industry_flags(self):
        if self.alcohol_industry and self.beverage_industry:
            frappe.throw(
                "Alcohol Industry and Beverage Industry cannot both be enabled. "
                "Choose one excise calculation mode.",
                title="EFRIS Settings Error",
            )


# ── Whitelisted API methods (called from JS buttons) ─────────────────────────

@frappe.whitelist()
def test_connection(company: str) -> dict:
    """
    SAP B1 Test Connection: server time (T101), sign in (T103) for the
    taxpayer, branch and device URA holds, then a session key (T104).
    Warns when URA's TIN is not the one in these settings.
    """
    from frappe.utils import escape_html

    from efris_integration.api.auth import get_symmetric_key, sign_in
    from efris_integration.api.base import EFRISClient

    settings = _get_settings(company)
    rows = []
    try:
        server_time = EFRISClient(company=company).call("T101", {}, doc_type="EFRIS Settings", doc_name=company)
        rows.append(("Server time", server_time.get("currentTime") if isinstance(server_time, dict) else server_time))
        info = sign_in(company)
        payer, branch, device = info.get("taxpayer") or {}, info.get("branch") or {}, info.get("device") or {}
        ura_tin = str(payer.get("tin") or payer.get("taxpayerTin") or "").strip()
        rows += [("Taxpayer", payer.get("legalName") or payer.get("businessName")), ("TIN", ura_tin),
                 ("Branch", branch.get("branchName") or branch.get("branchId")),
                 ("Device", device.get("deviceNo") or settings.get("device_no"))]
        get_symmetric_key(company, force=True)   # T103 reset the session
        rows.append(("Session key", "OK"))
    except Exception as e:
        return {"success": False, "message": escape_html(str(e))}

    html = "<table class='table table-bordered table-sm'>" + "".join(
        f"<tr><td>{escape_html(k)}</td><td>{escape_html(str(v or '-'))}</td></tr>" for k, v in rows) + "</table>"
    own_tin = (settings.get("tin") or "").strip()
    if ura_tin and own_tin and ura_tin != own_tin:
        html += (f"<p class='text-danger'>URA signed this device in as TIN {escape_html(ura_tin)}, "
                 f"but these settings have TIN {escape_html(own_tin)}. Check the TIN and Device No.</p>")
        return {"success": False, "message": html}
    return {"success": True, "message": html}


@frappe.whitelist()
def fetch_exchange_rates(company: str) -> dict:
    """Manually trigger URA exchange rate sync (T126) for the given company."""
    try:
        from efris_integration.api.exchange_rate import sync_exchange_rates
        sync_exchange_rates(company=company)
        return {"success": True, "message": "Exchange rates synced successfully."}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "EFRIS Fetch Exchange Rates")
        return {"success": False, "message": str(e)}


# ── Public helper used across the entire integration ──────────────────────────

def get_settings(company: str) -> "EFRISSettings":
    """
    Return the EFRIS Settings document for the given company.
    Raises a user-friendly error if not configured.
    """
    return _get_settings(company)


def _get_settings(company: str) -> "EFRISSettings":
    if not company:
        frappe.throw("Company is required to load EFRIS Settings.")
    if not frappe.db.exists("EFRIS Settings", company):
        frappe.throw(
            f"EFRIS Settings not configured for company <b>{company}</b>. "
            "Please create an EFRIS Settings record for this company first.",
            title="EFRIS Not Configured",
        )
    return frappe.get_doc("EFRIS Settings", company)
