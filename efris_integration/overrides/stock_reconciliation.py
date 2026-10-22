"""
Stock Reconciliation on_submit → URA EFRIS (SAP B1 Inventory Posting).

Opening Stock is sent as T131 stock in (type 104); a count is sent as the
difference per line: stock in for increases, stock out for decreases.
See api/stock.py.
"""

from efris_integration.api.stock import auto_submit


def on_submit(doc, method):
    auto_submit(doc)
