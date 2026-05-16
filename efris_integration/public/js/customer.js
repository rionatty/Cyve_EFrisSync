/**
 * EFRIS Customer form enhancements.
 *
 * "Verify TIN" is a Button-type custom field placed inline next to the
 * Tax ID field — visible on the form without opening any menu.
 *
 * Mirrors SAP B1: Business Partner → Verify TIN button next to Name/TIN.
 */

frappe.ui.form.on("Customer", {

    // ── Verify TIN button click (inline Button field) ─────────────────────────
    efris_verify_tin_btn(frm) {
        efris_verify_tin(frm);
    },

    // ── Keep legacy efris_buyer_type in sync when Business Type changes ───────
    efris_business_type(frm) {
        frm.set_value("efris_buyer_type", frm.doc.efris_business_type || "");
    },
});


// ─── Verify TIN logic ─────────────────────────────────────────────────────────

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
            message:   __("Could not determine the company. Please set it on the customer."),
            indicator: "orange",
        });
        return;
    }

    frappe.dom.freeze(__("Verifying TIN with URA…"));

    frappe.call({
        method:  "efris_integration.api.customer.verify_tin",
        args:    { tin, company },
        callback(r) {
            frappe.dom.unfreeze();
            if (r.exc) return;

            const d = r.message || {};
            if (d.status === "ok") {
                frappe.msgprint({
                    title:     __("TIN Verified ✓"),
                    message:   `
                        <table class="table table-bordered" style="margin-top:8px">
                          <tr><td><b>TIN</b></td><td>${d.tin || tin}</td></tr>
                          <tr><td><b>Name</b></td><td>${d.taxpayer_name || "—"}</td></tr>
                          <tr><td><b>Status</b></td><td>${d.status_desc || "Active"}</td></tr>
                        </table>`,
                    indicator: "green",
                });
                // Auto-fill Invoice Name if URA returned a taxpayer name
                if (d.taxpayer_name && !frm.doc.efris_invoice_name) {
                    frm.set_value("efris_invoice_name", d.taxpayer_name);
                }
            } else {
                frappe.msgprint({
                    title:     __("TIN Not Found"),
                    message:   d.message || __("TIN could not be verified with URA."),
                    indicator: "red",
                });
            }
        },
        error() {
            frappe.dom.unfreeze();
        },
    });
}
