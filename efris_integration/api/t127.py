"""
T127 — Industry Configuration  (per-company)

Mirrors: URASetting.cs (USE T127 checkbox), T127.cs

When "USE T127 – On AR Invoice" is checked in EFRIS Settings, the system
calls T127 before each AR Invoice submission to fetch the current industry
excise rates and configuration from URA.

The T127 response updates:
  - Excise rate schedules (by goods category)
  - Beverage / alcohol classification codes
  - Any active URA notices affecting duty rates

Results are cached per (site, company) and refreshed on each submission.
Cache is invalidated on EFRIS Settings save.
"""

import frappe

from efris_integration.api.base import EFRISClient
from efris_integration.utils.helpers import get_efris_settings


# Module-level cache: {(site, company): response_dict}
_T127_CACHE: dict = {}


@frappe.whitelist()
def get_industry_config(company: str, force_refresh: bool = False) -> dict:
    """
    Call T127 for the given company and return the industry configuration dict.
    Results are cached per (site, company) for the current process lifetime.
    Pass force_refresh=True to bypass cache.
    """
    if not company:
        frappe.throw("Company is required for T127 industry config lookup.")

    cache_key = (frappe.local.site, company)
    if not force_refresh and cache_key in _T127_CACHE:
        return _T127_CACHE[cache_key]

    client = EFRISClient(company=company)
    result = client.call("T127", doc_type="EFRIS Settings", doc_name=company)

    _T127_CACHE[cache_key] = result
    return result


def get_excise_rate_from_t127(goods_category_id: str, company: str) -> dict | None:
    """
    Look up the excise rate for a specific goods category from the T127 response.
    Returns None if T127 is not enabled or category not found.
    """
    settings = get_efris_settings(company)
    if not settings.use_t127:
        return None

    config = get_industry_config(company=company)
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
    Called automatically on EFRIS Settings save (doc_events hook in hooks.py),
    so Frappe passes (doc, method) — both are optional here.
    When called with a doc, only the cache entry for that company is cleared;
    otherwise the entire cache is wiped.
    """
    global _T127_CACHE
    if doc is not None:
        # Selectively clear just the affected company
        company   = getattr(doc, "name", None) or getattr(doc, "company", None)
        site      = frappe.local.site
        cache_key = (site, company)
        _T127_CACHE.pop(cache_key, None)
    else:
        _T127_CACHE = {}
