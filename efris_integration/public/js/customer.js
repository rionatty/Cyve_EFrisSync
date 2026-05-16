/**
 * EFRIS Customer form enhancements.
 *
 * "Verify TIN" is added to the form toolbar (next to Save / Actions) via
 * frm.add_custom_button() — mirrors SAP B1: Business Partner → Verify TIN
 * button in the top action bar.
 *
 * On success the dialog auto-fills:
 *   • efris_invoice_name  ← taxpayer registered name
 *   • efris_business_type ← derived from taxpayer type (B2B / B2C / B2G / B2F)
 *   • efris_buyer_type    ← kept in sync with efris_business_type (legacy)
 */

frappe.ui.form.on("Customer", {

    // ── Toolbar button — added (and styled) on every form refresh ─────────────
    refresh(frm) {
        // add_custom_button returns the <button> jQuery element.
        // Assign first, style separately so a null return never throws.
        const $btn = frm.add_custom_button(__("Verify TIN"), () => {
            efris_verify_tin(frm);
        });

        if ($btn && $btn.length) {
            $btn
                .removeClass("btn-default btn-secondary")
                .addClass("btn-primary")
                .css({ "font-weight": "600" });
        }
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
//   2. Call T119 (Customer Information Query) with { "tin": "<TIN>" }
//   3. Decrypt + parse taxpayerName, taxpayerStatus, taxpayerType
//   4. Show result dialog; auto-fill BP Name / Taxpayer Name field on OK
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

    // Resolve company — customer may not carry it directly; fall back to default
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
        freeze:  false,          // we manage freeze manually for better UX
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
                        || __("URA could not find a taxpayer registered for TIN <b>{0}</b>.", [tin]),
                    indicator: "red",
                });
            }
        },
        error() {
            frappe.dom.unfreeze();
        },
    });
}


// ─── Result dialog — SAP B1 shows a modal with taxpayer details then offers ───
// to auto-fill the BP Name / Invoice Name.  We do the same.

function _show_verified_dialog(frm, queried_tin, d) {
    const returned_tin   = d.tin           || queried_tin;
    const taxpayer_name  = d.taxpayer_name || "";
    const status_desc    = d.status_desc   || "Active";
    const taxpayer_type  = d.taxpayer_type || "";   // URA field: taxpayerType

    // Map URA taxpayerType → EFRIS business type (SAP B1 mapping table)
    const buyer_type = _map_taxpayer_type(taxpayer_type);

    const already_has_name = !!(frm.doc.efris_invoice_name || "").trim();

    // Build detail rows
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

    // Offer auto-fill actions (SAP B1 offers "Copy Name" checkbox on OK)
    let auto_fill_note = "";
    if (taxpayer_name) {
        if (!already_has_name) {
            auto_fill_note = `<div class="alert alert-info" style="margin-top:8px;font-size:12px">
                <b>${__("Auto-fill")}</b>: Invoice Name will be set to
                "<i>${frappe.utils.escape_html(taxpayer_name)}</i>"
                ${buyer_type ? `and Business Type set to <b>${buyer_type}</b>` : ""}.
            </div>`;
        } else {
            auto_fill_note = `<div class="alert alert-warning" style="margin-top:8px;font-size:12px">
                ${__("Invoice Name already set — URA name not overwritten.")}
            </div>`;
        }
    }

    const dialog = new frappe.ui.Dialog({
        title:   __("TIN Verified ✓"),
        fields: [
            {
                fieldtype: "HTML",
                options:   rows + auto_fill_note,
            },
        ],
        primary_action_label: __("OK"),
        primary_action() {
            // Auto-fill Invoice Name (SAP B1: copies taxpayerName to BP Name field)
            if (taxpayer_name && !already_has_name) {
                frm.set_value("efris_invoice_name", taxpayer_name);
            }
            // Auto-fill Business Type if URA returned a taxpayer type
            if (buyer_type) {
                frm.set_value("efris_business_type", buyer_type);
                frm.set_value("efris_buyer_type",    buyer_type);
            }
            dialog.hide();
        },
    });

    // Tint the dialog header green
    dialog.$wrapper.find(".modal-header").css("background", "#d4edda");
    dialog.show();
}


// ─── Map URA taxpayerType → EFRIS buyer-type code ────────────────────────────
// SAP B1 mapping (T119ResponseMapper.cs):
//   "INDIVIDUAL"        → B2C
//   "COMPANY"           → B2B
//   "GOVERNMENT"        → B2G
//   "FOREIGN_NATIONAL"  → B2F

function _map_taxpayer_type(taxpayer_type) {
    if (!taxpayer_type) return "";
    const t = taxpayer_type.toUpperCase();
    if (t.includes("GOVERNMENT"))           return "B2G";
    if (t.includes("FOREIGN"))              return "B2F";
    if (t.includes("COMPANY")
        || t.includes("CORPORATE")
        || t.includes("BUSINESS"))          return "B2B";
    if (t.includes("INDIVIDUAL")
        || t.includes("PERSON"))            return "B2C";
    return "";
}
