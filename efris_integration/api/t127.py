"""
T127 — Industry Configuration

Mirrors: URASetting.cs (USE T127 checkbox), T127.cs

When "USE T127 – On AR Invoice" is checked in EFRIS Settings, the system
calls T127 before each AR Invoice submission to fetch the current industry
excise rates and configuration from URA.

The T127 response updates:
  - Excise rate schedules (by goods category)
  - Beverage / alcohol classification codes
  - Any active URA notices affecting duty rates

Results are cached for the session (in-memory) and refreshed on each submission.
"""

import frappe

from efris_integration.api.base import EFRISClient


# Module-level cache: {site: {category_id: rate_info}}
_T127_CACHE: dict = {}


@frappe.whitelist()
def get_industry_config(force_refresh: bool = False) -> dict:
    """
    Call T127 and return the industry configuration dict.
    Results are cached per Frappe site for the current process lifetime.
    Pass force_refresh=True to bypass cache.
    """
    site = frappe.local.site
    if not force_refresh and site in _T127_CACHE:
        return _T127_CACHE[site]

    client = EFRISClient()
    result = client.call("T127", doc_type="EFRIS Settings", doc_name="T127")

    _T127_CACHE[site] = result
    return result


def get_excise_rate_from_t127(goods_category_id: str) -> dict | None:
    """
    Look up the excise rate for a specific goods category from the T127 response.
    Returns None if T127 is not enabled or category not found.
    """
    settings = frappe.get_single("EFRIS Settings")
    if not settings.use_t127:
        return None

    config = get_industry_config()
    # T127 response structure varies by URA version; handle common layouts
    rate_list = (
        config.get("goodsCategoryRates")
        or config.get("rateList")
        or config.get("rates")
        or []
    )

    for entry in rate_list:
        cat_id = str(entry.get("goodsCategoryId") or entry.get("categoryId") or "")
        if cat_id == str(goods_category_id):
            return entry

    return None


def invalidate_cache(doc=None, method=None):
    """
    Clear the T127 in-memory cache.
    Called automatically on EFRIS Settings save (doc_events hook),
    so Frappe passes (doc, method) — both are optional here.
    """
    global _T127_CACHE
    _T127_CACHE = {}
