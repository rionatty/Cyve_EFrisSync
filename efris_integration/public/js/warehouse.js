/**
 * EFRIS Warehouse form enhancements.
 *
 * "Get from URA" toolbar button — mirrors SAP B1: Warehouse master →
 * URA tab → "Get Details from URA" button.
 *
 * Queries URA T127 (Branch / Industry Config) and fills:
 *   • URA Warehouse   (efris_ura_warehouse)
 *   • URA Branch Id   (efris_branch_id)
 *   • URA Branch Name (efris_branch_name)
 */

frappe.ui.form.on("Warehouse", {

    refresh(frm) {
        const $btn = frm.add_custom_button(__("Get from URA"), () => {
            efris_get_warehouse_details(frm);
        });

        if ($btn && $btn.length) {
            $btn.removeClass("btn-default btn-secondary")
                .addClass("btn-primary")
                .css("font-weight", "600");
        }
    },
});


// ─── Get warehouse / branch details from URA ──────────────────────────────────

function efris_get_warehouse_details(frm) {
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
            message:   __("Please set the Company on this Warehouse before querying URA."),
            indicator: "orange",
        });
        return;
    }

    frappe.dom.freeze(__("Fetching warehouse details from URA…"));

    frappe.call({
        method: "efris_integration.api.warehouse.get_ura_details",
        args:   { warehouse_name: frm.doc.name, company },
        freeze: false,
        callback(r) {
            frappe.dom.unfreeze();
            if (r.exc) return;

            const d = r.message || {};

            if (d.status === "ok") {
                _apply_warehouse_details(frm, d);
            } else {
                // Show raw fields if available so we can see what URA returned
                _show_not_found(frm, d);
            }
        },
        error() {
            frappe.dom.unfreeze();
        },
    });
}


// ─── Apply details returned by URA to the form ────────────────────────────────

function _apply_warehouse_details(frm, d) {
    const raw  = d.raw_fields || {};
    const rows = _build_detail_table(d, raw);

    const dialog = new frappe.ui.Dialog({
        title:   __("URA Warehouse Details"),
        fields:  [{ fieldtype: "HTML", options: rows }],
        primary_action_label: __("Apply"),
        primary_action() {
            if (d.ura_warehouse) frm.set_value("efris_ura_warehouse", d.ura_warehouse);
            if (d.branch_id)     frm.set_value("efris_branch_id",     d.branch_id);
            if (d.branch_name)   frm.set_value("efris_branch_name",   d.branch_name);

            dialog.hide();
            frappe.show_alert({
                message:   __("URA warehouse details applied — please Save."),
                indicator: "green",
            }, 5);
        },
    });

    dialog.$wrapper.find(".modal-header").css("background", "#d4edda");
    dialog.show();
}


// ─── Build the HTML table for the dialog ─────────────────────────────────────

function _build_detail_table(d, raw) {
    let rows = `
        <table class="table table-bordered" style="margin-top:8px;font-size:13px">
          <tr><th style="width:40%">${__("URA Warehouse")}</th>
              <td><b>${frappe.utils.escape_html(d.ura_warehouse || "—")}</b></td></tr>
          <tr><th>${__("Branch Id")}</th>
              <td>${frappe.utils.escape_html(d.branch_id || "—")}</td></tr>
          <tr><th>${__("Branch Name")}</th>
              <td>${frappe.utils.escape_html(d.branch_name || "—")}</td></tr>
        </table>`;

    // Show raw fields if name is missing (helps identify correct field names)
    if (!d.branch_name && Object.keys(raw).length) {
        rows += `<p class="text-muted" style="font-size:11px;margin-top:8px">
                     Raw URA response fields:</p>
                 <table class="table table-bordered" style="font-size:11px">`;
        for (const [k, v] of Object.entries(raw)) {
            rows += `<tr><td style="color:#888">${frappe.utils.escape_html(k)}</td>
                         <td>${frappe.utils.escape_html(
                             typeof v === "object" ? JSON.stringify(v) : String(v)
                         )}</td></tr>`;
        }
        rows += `</table>`;
    }

    return rows;
}


// ─── Not-found / error dialog ─────────────────────────────────────────────────

function _show_not_found(frm, d) {
    const raw  = d.raw_fields || {};
    let   body = `<p>${frappe.utils.escape_html(
                      d.message || __("URA returned no details for this warehouse.")
                  )}</p>`;

    if (Object.keys(raw).length) {
        body += `<p class="text-muted" style="font-size:11px">Raw URA response:</p>
                 <table class="table table-bordered" style="font-size:11px">`;
        for (const [k, v] of Object.entries(raw)) {
            body += `<tr><td style="color:#888">${frappe.utils.escape_html(k)}</td>
                         <td>${frappe.utils.escape_html(
                             typeof v === "object" ? JSON.stringify(v) : String(v)
                         )}</td></tr>`;
        }
        body += `</table>`;
    }

    frappe.msgprint({ title: __("URA — No Branch Details"), message: body, indicator: "orange" });
}
