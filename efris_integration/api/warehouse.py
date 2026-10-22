"""
Warehouse / Branch EFRIS helpers.

get_ura_branch() — Call T138 (Get All Branches), match the ERPNext
                   warehouse name against URA branchName, and return
                   the matched branchId + branchName.

EFRIS API spec (section 38):
  Interface Code   : T138
  Request Encrypted: N   → encrypt=False
  Response Encrypted: Y  → client decrypts automatically
  Request Message  : NULL → empty payload {}
  Response         : [{ "branchId": "...", "branchName": "..." }, ...]

Mirrors SAP B1: Warehouse → URA tab → "Get Details from URA" button
which calls T138 and matches by branch name.
"""

import frappe

from efris_integration.api.base import EFRISClient, EFRISAPIError
from efris_integration.api.session import ensure_session
from efris_integration.utils.helpers import log_efris


@frappe.whitelist()
def get_ura_branch(warehouse_name: str, company: str) -> dict:
    """
    Call T138 to fetch all URA branches, match against the ERPNext
    warehouse name, and return the matched branchId / branchName.

    Matching logic (in order):
      1. Exact match (case-insensitive)
      2. ERPNext name is contained in URA branch name
      3. URA branch name is contained in ERPNext name
      4. No match → return full branch list so the user can pick manually

    Returns:
        {
          "status":      "ok" | "no_match" | "error",
          "branch_id":   "206637525568955296",
          "branch_name": "Mr. STEPHEN BUNJO",
          "matched":     True | False,
          "all_branches": [...],   # full list for manual selection
          "message":     "human-readable summary"
        }
    """
    if not warehouse_name:
        frappe.throw("Warehouse name is required.", title="Get URA Branch")
    if not company:
        frappe.throw("Company is required.", title="Get URA Branch")

    client = EFRISClient(company=company)

    try:
        ensure_session(client, company)
    except Exception as exc:
        frappe.throw(str(exc), title="EFRIS Session Error")

    # ── T138: Get All Branches ────────────────────────────────────────────────
    # Request Encrypted = N  →  encrypt=False
    # Response Encrypted = Y  →  client decrypts response automatically
    # Request Message = NULL  →  empty payload
    payload = {}
    try:
        result = client.call(
            "T138",
            payload=payload,
            encrypt=False,      # request NOT encrypted per spec
            sign=True,          # RSA signing always required
            doc_type="Warehouse",
            doc_name=warehouse_name,
        )
    except EFRISAPIError as exc:
        log_efris("Warehouse", warehouse_name, "T138", payload, {}, "Failed",
                  f"[{exc.code}] {exc.message}")
        return {
            "status":  "error",
            "message": f"[{exc.code}] {exc.message}",
        }
    except Exception as exc:
        frappe.throw(str(exc), title="EFRIS Error")

    # ── Normalise response to a list ─────────────────────────────────────────
    if isinstance(result, dict):
        # Might be wrapped: { "branchList": [...] } or { "data": [...] }
        branches = (
            result.get("branchList")
            or result.get("branches")
            or result.get("data")
            or [result]
        )
    elif isinstance(result, list):
        branches = result
    else:
        branches = []

    frappe.logger("efris").info(
        f"T138 returned {len(branches)} branch(es) for company {company}"
    )
    log_efris("Warehouse", warehouse_name, "T138", payload,
              {"count": len(branches)}, "Success")

    # ── Build normalised branch list ──────────────────────────────────────────
    all_branches = []
    for b in branches:
        if not isinstance(b, dict):
            continue
        bid   = str(b.get("branchId")   or b.get("branch_id")   or "").strip()
        bname = str(b.get("branchName") or b.get("branch_name") or "").strip()
        if bid or bname:
            all_branches.append({"branch_id": bid, "branch_name": bname})

    if not all_branches:
        return {
            "status":      "error",
            "message":     "EFRIS returned no branches for this company.",
            "all_branches": [],
        }

    # ── Match ERPNext warehouse name against URA branch names ─────────────────
    # Get the human-readable warehouse name (not the docname slug)
    erp_name = (
        frappe.db.get_value("Warehouse", warehouse_name, "warehouse_name")
        or warehouse_name
    ).strip().upper()

    matched_branch = None

    # Pass 1: exact match
    for b in all_branches:
        if b["branch_name"].upper() == erp_name:
            matched_branch = b
            break

    # Pass 2: ERPNext name is a substring of URA name
    if not matched_branch:
        for b in all_branches:
            if erp_name in b["branch_name"].upper():
                matched_branch = b
                break

    # Pass 3: URA name is a substring of ERPNext name
    if not matched_branch:
        for b in all_branches:
            if b["branch_name"].upper() in erp_name:
                matched_branch = b
                break

    if matched_branch:
        return {
            "status":       "ok",
            "branch_id":    matched_branch["branch_id"],
            "branch_name":  matched_branch["branch_name"],
            "matched":      True,
            "all_branches": all_branches,
            "message":      (f"Matched: {matched_branch['branch_name']} "
                             f"(ID: {matched_branch['branch_id']})"),
        }

    # No name match — return full list so UI can let the user pick
    return {
        "status":       "no_match",
        "branch_id":    "",
        "branch_name":  "",
        "matched":      False,
        "all_branches": all_branches,
        "message":      (f"No URA branch matched '{erp_name}'. "
                         f"Please select from the list below."),
    }
