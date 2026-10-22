"""
Purchase Receipt on_submit → URA EFRIS (SAP B1 GRPO / Goods Return).

A receipt is sent as T131 stock in (Stock-In Type from the EFRIS tab, default
Local Purchase); a purchase return as T131 stock out. See api/stock.py.
"""

from efris_integration.api.stock import auto_submit


def on_submit(doc, method):
    auto_submit(doc)
