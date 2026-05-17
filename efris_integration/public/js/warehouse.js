/**
 * EFRIS Warehouse form — Get Branch from URA.
 *
 * Toolbar button "Get Branch ID" calls T138 (Get All Branches),
 * matches the ERPNext warehouse name against URA branchName, and
 * fills efris_branch_id + efris_branch_name automatically.
 *
 * If no name match is found, a selection dialog shows all URA branches
 * so the user can pick the correct one manually.
 *
 * Mirrors SAP B1: Warehouse → URA tab → "Get Details from URA".
 */

frappe.ui.form.on("Warehouse", {

    refresh(frm) {
        // ERPNext's own warehouse.js runs AFTER this refresh and calls
        // set_secondary_action for "Disable" / "Convert to Group", which
        // overwrites anything we place there.
        // Fix: defer our injection by 200 ms so we run last and the button
        // sticks. We remove any stale instance first to prevent stacking.
        setTimeout(() => _inject_branch_btn(frm), 200);
    },
});

function _inject_branch_btn(frm) {
    const CLASS = "efris-get-branch-btn";

    // Clean up any button left by a previous refresh
    $(frm.page.wrapper).find(`.${CLASS}`).remove();

    const $btn = $("<button>")
        .addClass(`btn btn-sm btn-primary ${CLASS}`)
        .css({ "margin-right": "4px", "font-weight": "600" })
        .text(__("Get Branch ID from URA"))
        .on("click", () => efris_get_branch(frm));

    // Insert before the green Save button (.primary-action)
    const $save = $(frm.page.wrapper)
        .find(".page-actions .primary-action, .page-actions .btn-primary")
        .first();

    if ($save.length) {
        $save.before($btn);
    } else {
        // Fallback: prepend to the page-actions bar
        $(frm.page.wrapper).find(".page-actions").prepend($btn);
    }
}


// ─── Main handler ─────────────────────────────────────────────────────────────

function efris_get_branch(frm) {
    if (frm.doc.__islocal) {
        frappe.msgprint({
            title:     __("Save First"),
            message:   __("Please save the Warehouse before querying URA."),
            indicator: "orange",
        });
        return;
    }

    const company = frm.doc.company
        || frappe.defaults.get_default("company")
        || "";

    if (!company) {
        frappe.msgprint({
            title:     __("Company Required"),
            message:   __("Set the Company on this warehouse before querying URA."),
            indicator: "orange",
        });
        return;
    }

    frappe.dom.freeze(__("Fetching branches from URA (T138)…"));

    frappe.call({
        method: "efris_integration.api.warehouse.get_ura_branch",
        args:   { warehouse_name: frm.doc.name, company },
        freeze: false,
        callback(r) {
            frappe.dom.unfreeze();
            if (r.exc) return;

            const d = r.message || {};

            if (d.status === "error") {
                frappe.msgprint({
                    title:     __("URA Error"),
                    message:   d.message || __("T138 call failed."),
                    indicator: "red",
                });
                return;
            }

            if (d.status === "ok" && d.matched) {
                // Auto-matched — show confirmation dialog
                _show_match_dialog(frm, d);
            } else {
                // No auto-match — let user pick from full list
                _show_pick_dialog(frm, d);
            }
        },
        error() {
            frappe.dom.unfreeze();
        },
    });
}


// ─── Auto-match confirmed ─────────────────────────────────────────────────────

function _show_match_dialog(frm, d) {
    const dialog = new frappe.ui.Dialog({
        title:   __("URA Branch Matched"),
        fields:  [{
            fieldtype: "HTML",
            options: `
                <table class="table table-bordered" style="margin-top:8px;font-size:13px">
                  <tr><th style="width:40%">${__("Branch Id")}</th>
                      <td><b>${frappe.utils.escape_html(d.branch_id)}</b></td></tr>
                  <tr><th>${__("Branch Name")}</th>
                      <td>${frappe.utils.escape_html(d.branch_name)}</td></tr>
                </table>
                <div class="alert alert-success" style="font-size:12px;margin-top:6px">
                    ${__("Name matched automatically. Click Apply to save.")}
                </div>`,
        }],
        primary_action_label: __("Apply"),
        primary_action() {
            _apply(frm, d.branch_id, d.branch_name);
            dialog.hide();
        },
    });

    dialog.$wrapper.find(".modal-header").css("background", "#d4edda");
    dialog.show();
}


// ─── Manual pick when no name match ──────────────────────────────────────────

function _show_pick_dialog(frm, d) {
    const branches = d.all_branches || [];

    // Build option list
    const options = branches.map(b =>
        `<option value="${frappe.utils.escape_html(b.branch_id)}">
             ${frappe.utils.escape_html(b.branch_name)}
             &nbsp;(${frappe.utils.escape_html(b.branch_id)})
         </option>`
    ).join("");

    const dialog = new frappe.ui.Dialog({
        title:   __("Select URA Branch"),
        fields:  [{
            fieldtype: "HTML",
            options: `
                <p style="color:#e67e22;font-size:13px">
                    <b>${__("No automatic match found.")}</b><br>
                    ${frappe.utils.escape_html(d.message || "")}
                </p>
                <label style="font-size:13px"><b>${__("Select Branch")}</b></label>
                <select id="efris-branch-select" class="form-control"
                        style="margin-top:4px;font-size:13px">
                    <option value="">— ${__("Select a branch")} —</option>
                    ${options}
                </select>`,
        }],
        primary_action_label: __("Apply Selected"),
        primary_action() {
            const sel = dialog.$wrapper.find("#efris-branch-select");
            const bid = sel.val();
            if (!bid) {
                frappe.msgprint(__("Please select a branch first."));
                return;
            }
            const bname = sel.find("option:selected").text()
                             .replace(/\s*\(.*\)$/, "").trim();
            _apply(frm, bid, bname);
            dialog.hide();
        },
    });

    dialog.show();
}


// ─── Write values to form and alert ──────────────────────────────────────────

function _apply(frm, branch_id, branch_name) {
    if (branch_id)   frm.set_value("efris_branch_id",   branch_id);
    if (branch_name) frm.set_value("efris_branch_name", branch_name);

    frappe.show_alert({
        message:   __("Branch ID applied — please Save."),
        indicator: "green",
    }, 5);
}
