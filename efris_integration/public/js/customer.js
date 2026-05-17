/**
 * EFRIS Customer form — Verify TIN + EFRIS-tab helpers.
 *
 * "Verify TIN" is placed in the top-right page-action bar (same row as Save)
 * using frm.page.set_secondary_action() — the Frappe API for adding a
 * standalone button next to the primary Save button.
 *
 * Mirrors SAP B1: Business Partner → Verify TIN button in the top toolbar.
 */

frappe.ui.form.on("Customer", {

    // ── Top-right page-action button ─────────────────────────────────────────
    refresh(frm) {
        // set_secondary_action places the button in the page head
        // next to Save — same slot used by Frappe's own "Edit" / "Amend" buttons.
        frm.page.set_secondary_action(
            __("Verify TIN"),
            () => efris_verify_tin(frm),
            "fa fa-check-circle"          // icon (FontAwesome)
        );
    },

    // ── Keep legacy efris_buyer_type in sync when Business Type changes ───────
    efris_business_type(frm) {
        frm.set_value("efris_buyer_type", frm.doc.efris_business_type || "");
    },
});


// ─── Verify TIN — mirrors SAP B1 URA Business-Partner TIN verification ────────
//
// SAP B1 flow (BusinessPartnerEfrisTabPage.cs → VerifyTin_Click):
//   1. Read TIN from BP TaxID field
//   2. POST T119  { "tin": "<TIN>" }
//   3. Decrypt + parse taxpayerName, taxpayerStatus, taxpayerType
//   4. Show result dialog; auto-fill BP Name / Taxpayer Name on OK
//   5. Map taxpayerType → buyerType (B2B / B2C / B2G / B2F)

function efris_verify_tin(frm) {
    const tin = (frm.doc.tax_id || "").trim();

    if (!tin) {
        frappe.msgprint({
            title:     __("TIN Required"),
            message:   __("Please enter the customer's <b>Tax ID (TIN)</b> before verifying."),
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
            message:   __("Could not determine the company. Please set a default company in System Settings."),
            indicator: "orange",
        });
        return;
    }

    frappe.dom.freeze(__("Verifying TIN with URA…"));

    frappe.call({
        method:  "efris_integration.api.customer.verify_tin",
        args:    { tin, company },
        freeze:  false,
        callback(r) {
            frappe.dom.unfreeze();
            if (r.exc) return;

            const d = r.message || {};

            if (d.status === "ok") {
                _show_verified_dialog(frm, tin, d);
            } else {
                frappe.msgprint({
                    title:     __("TIN Not Found"),
                    message:   d.message
                        || __("URA could not find a taxpayer for TIN <b>{0}</b>.", [tin]),
                    indicator: "red",
                });
            }
        },
        error() {
            frappe.dom.unfreeze();
        },
    });
}


// ─── Result dialog ────────────────────────────────────────────────────────────

function _show_verified_dialog(frm, queried_tin, d) {
    const returned_tin  = d.tin           || queried_tin;
    const taxpayer_name = d.taxpayer_name || "";
    const status_desc   = d.status_desc   || "Active";
    const taxpayer_type = d.taxpayer_type || "";

    const buyer_type        = _map_taxpayer_type(taxpayer_type);
    const already_has_name  = !!(frm.doc.efris_invoice_name || "").trim();

    let rows = `
        <table class="table table-bordered" style="margin-top:8px;font-size:13px">
          <tr><th style="width:38%">${__("TIN")}</th>
              <td><b>${frappe.utils.escape_html(returned_tin)}</b></td></tr>
          <tr><th>${__("Registered Name")}</th>
              <td>${frappe.utils.escape_html(taxpayer_name || "—")}</td></tr>
          <tr><th>${__("Status")}</th>
              <td><span class="indicator-pill ${status_desc === "Active" ? "green" : "orange"} no-indicator-dot"
                        style="padding:2px 8px">${frappe.utils.escape_html(status_desc)}</span></td></tr>`;

    if (taxpayer_type) {
        rows += `<tr><th>${__("Taxpayer Type")}</th>
                     <td>${frappe.utils.escape_html(taxpayer_type)}
                         ${buyer_type ? `<i class="text-muted"> → ${buyer_type}</i>` : ""}</td></tr>`;
    }
    rows += `</table>`;

    let auto_fill_note = "";
    if (taxpayer_name) {
        auto_fill_note = !already_has_name
            ? `<div class="alert alert-info" style="margin-top:8px;font-size:12px">
                 <b>${__("Auto-fill")}</b>: Invoice Name ← "<i>${frappe.utils.escape_html(taxpayer_name)}</i>"
                 ${buyer_type ? ` &amp; Business Type ← <b>${buyer_type}</b>` : ""}.
               </div>`
            : `<div class="alert alert-warning" style="margin-top:8px;font-size:12px">
                 ${__("Invoice Name already set — URA name not overwritten.")}
               </div>`;
    }

    const dialog = new frappe.ui.Dialog({
        title:   __("TIN Verified ✓"),
        fields:  [{ fieldtype: "HTML", options: rows + auto_fill_note }],
        primary_action_label: __("OK"),
        primary_action() {
            if (taxpayer_name && !already_has_name) {
                frm.set_value("efris_invoice_name", taxpayer_name);
            }
            if (buyer_type) {
                frm.set_value("efris_business_type", buyer_type);
                frm.set_value("efris_buyer_type",    buyer_type);
            }
            dialog.hide();
        },
    });

    dialog.$wrapper.find(".modal-header").css("background", "#d4edda");
    dialog.show();
}


// ─── taxpayerType → EFRIS buyer-type (SAP B1 T119ResponseMapper) ─────────────

function _map_taxpayer_type(taxpayer_type) {
    if (!taxpayer_type) return "";
    const t = taxpayer_type.toUpperCase();
    if (t.includes("GOVERNMENT"))                                    return "B2G";
    if (t.includes("FOREIGN"))                                       return "B2F";
    if (t.includes("COMPANY") || t.includes("CORPORATE")
        || t.includes("BUSINESS"))                                   return "B2B";
    if (t.includes("INDIVIDUAL") || t.includes("PERSON"))           return "B2C";
    return "";
}
