"""
Kept for older cached form scripts that still call
efris_integration.api.stock_in.upload_stock_in. All stock sending now lives in
api/stock.py.
"""

import frappe

from efris_integration.api.stock import submit_stock_entry


@frappe.whitelist()
def upload_stock_in(stock_entry_name: str, company: str = "") -> dict:
    return submit_stock_entry(stock_entry_name)
