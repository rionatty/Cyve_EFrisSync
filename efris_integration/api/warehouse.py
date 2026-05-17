"""
Warehouse / Branch EFRIS helpers.

get_ura_details() — Query URA for branch/warehouse details registered
                    against this company's TIN and fill in:
                      • URA Warehouse  (efris_ura_warehouse)
                      • URA Branch Id  (efris_branch_id)
                      • URA Branch Name (efris_branch_name)

Mirrors SAP B1: Warehouse master → URA tab → "Get Details from URA" button.

URA T-code used: T127 (Industry / Branch Configuration Query).
The T127 response contains the registered branch list for the taxpayer.
If a branch_id is already set on the warehouse, the matching branch record
is returned; otherwise the first (default) branch is used.
"""

import frappe

from efris_integration.api.base import EFRISClient, EFRISAPIError
from efris_integration.api.session import ensure_session
from efris_integration.utils.helpers import get_efris_settings, log_efris


@frappe.whitelist()
def get_ura_details(warehouse_name: str, company: str) -> dict:
    """
    Query URA for warehouse/branch details and return them.

    Returns:
        {
          "status":        "ok" | "not_found",
          "ura_warehouse": "WH-001",
          "branch_id":     "10001",
          "branch_name":   "KAMPALA MAIN BRANCH",
          "message":       "human-readable summary"
        }
    """
    if not warehouse_name:
        frappe.throw("Warehouse name is required.", title="Get URA Details")
    if not company:
        frappe.throw("Company is required.", title="Get URA Details")

    settings = get_efris_settings(company)
    client   = EFRISClient(company=company)

    try:
        ensure_session(client, company)
    except Exception as exc:
        frappe.throw(str(exc), title="EFRIS Session Error")

    # Read the current branch_id from the warehouse (if already set)
    existing_branch_id = frappe.db.get_value(
        "Warehouse", warehouse_name, "efris_branch_id"
    ) or ""

    # ── T127: Branch / Industry Configuration Query ───────────────────────────
    # Payload matches SAP B1 T127Request: empty body fetches full config.
    payload = {}
    try:
        result = client.call(
            "T127",
            payload=payload,
            encrypt=True,
            sign=True,              # all URA T-codes require RSA signing
            doc_type="Warehouse",
            doc_name=warehouse_name,
        )
    except EFRISAPIError as exc:
        log_efris("Warehouse", warehouse_name, "T127", payload, {}, "Failed",
                  f"[{exc.code}] {exc.message}")
        return {
            "status":  "not_found",
            "message": f"[{exc.code}] {exc.message}",
        }
    except Exception as exc:
        frappe.throw(str(exc), title="EFRIS Get Warehouse Details Error")

    # ── Parse T127 response ───────────────────────────────────────────────────
    # T127 returns a dict or list of branch/industry records.
    if isinstance(result, list):
        branches = result
    elif isinstance(result, dict):
        # Try common wrapper keys
        branches = (
            result.get("branchList")
            or result.get("branches")
            or result.get("branchInfo")
            or [result]           # single record
        )
    else:
        branches = []

    frappe.logger("efris").info(
        f"T127 warehouse response for {warehouse_name}: {result}"
    )

    log_efris("Warehouse", warehouse_name, "T127", payload, result, "Success")

    # ── Match branch by existing branch_id or fall back to first branch ───────
    matched = None
    for branch in (branches or []):
        if not isinstance(branch, dict):
            continue
        bid = (
            branch.get("branchId")
            or branch.get("BranchId")
            or branch.get("branch_id")
            or ""
        )
        if existing_branch_id and str(bid) == str(existing_branch_id):
            matched = branch
            break

    if not matched and branches:
        matched = branches[0] if isinstance(branches[0], dict) else None

    if not matched:
        # T127 succeeded but returned no branch data
        return {
            "status":        "not_found",
            "ura_warehouse": "",
            "branch_id":     existing_branch_id,
            "branch_name":   "",
            "message":       "URA returned no branch details for this company.",
            "raw_fields":    result if isinstance(result, dict) else {},
        }

    # ── Extract fields (try all known URA field name variants) ───────────────
    branch_id = (
        matched.get("branchId")
        or matched.get("BranchId")
        or matched.get("branch_id")
        or existing_branch_id
        or ""
    )
    branch_name = (
        matched.get("branchName")
        or matched.get("BranchName")
        or matched.get("branch_name")
        or matched.get("name")
        or ""
    )
    ura_warehouse = (
        matched.get("warehouseCode")
        or matched.get("WarehouseCode")
        or matched.get("warehouse")
        or matched.get("branchCode")
        or matched.get("BranchCode")
        or branch_id
        or ""
    )

    return {
        "status":        "ok",
        "ura_warehouse": ura_warehouse,
        "branch_id":     branch_id,
        "branch_name":   branch_name,
        "message":       f"Branch {branch_id} — {branch_name}",
        "raw_fields":    matched,
    }
