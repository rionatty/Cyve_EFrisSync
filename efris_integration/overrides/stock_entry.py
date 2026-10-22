"""
Stock Entry on_submit → URA EFRIS (T131 stock in/out, T139 branch transfers).

What each entry type sends is decided in api/stock.py (_stock_entry_parts).
Failures never block the submit; the user can retry from EFRIS → Upload to EFRIS.

Mirrors: GoodsReceipt.cs, GoodsIssue.cs, IssueFromProduction.cs,
         ReceiptFromProduction.cs, InventoryTransfer.cs
"""

from efris_integration.api.stock import auto_submit


def on_submit(doc, method):
    auto_submit(doc)
