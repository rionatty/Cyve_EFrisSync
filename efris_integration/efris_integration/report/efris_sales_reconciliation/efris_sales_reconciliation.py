"""
EFRIS Sales Reconciliation — ERPNext sales vs URA (SAP B1 SalesReconciliation.cs).
See api/reconcile.py for how invoices are matched.
"""

from efris_integration.api.reconcile import run_report


def execute(filters=None):
    return run_report("Sales", filters)
