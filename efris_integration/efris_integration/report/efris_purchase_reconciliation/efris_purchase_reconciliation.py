"""
EFRIS Purchase Reconciliation — ERPNext purchases vs URA (SAP B1 PurchaseReconciliation.cs).
See api/reconcile.py for how invoices are matched.
"""

from efris_integration.api.reconcile import run_report


def execute(filters=None):
    return run_report("Purchases", filters)
