# Copyright (c) 2024, CyveTech and contributors
# EFRIS Settings — one document per company

import frappe
from frappe.model.document import Document


class EFRISSettings(Document):

    def validate(self):
        self._validate_required_fields()
        self._clean_keys()
        self._validate_industry_flags()

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
    """Test the EFRIS API connection for the given company (T101)."""
    _get_settings(company)  # validates it exists
    from efris_integration.api.base import EFRISClient
    client = EFRISClient(company=company)
    try:
        result = client.call("T101", {}, doc_type="EFRIS Settings", doc_name=company)
        return {"success": True, "message": f"Connection successful. Server time: {result}"}
    except Exception as e:
        return {"success": False, "message": str(e)}


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
