/**
 * EFRIS Customer form enhancements.
 *
 * Adds:
 *   • "Verify TIN" button — validates the customer's Tax ID (TIN) against URA
 *   • Auto-sync efris_business_type → efris_buyer_type (legacy invoice field)
 *
 * Mirrors SAP B1 Business Partner → EFRIS tab → Verify TIN button.
 */

frappe.ui.form.on("Customer", {

    // ── On form load / refresh ────────────────────────────────────────────────
    refresh(frm) {
        // "Verify TIN" button — placed in the form toolbar
        frm.add_custom_button(__("Verify TIN"), () => {
            efris_verify_tin(frm);
        }).addClass("btn-warning");
    },

    // ── Keep legacy efris_buyer_type in sync whenever Business Type changes ──
    efris_business_type(frm) {
        const bt = frm.doc.efris_business_type || "";
        // Extract the raw code (B2C, B2B, B2F, B2G) — no suffix in new field
        frm.set_value("efris_buyer_type", bt);
    },
});


// ─── Verify TIN ───────────────────────────────────────────────────────────────

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

    if (!frm.doc.company) {
        frappe.msgprint({
            title:     __("Company Required"),
            message:   __("Please set the customer's <b>Company</b> before verifying."),
            indicator: "orange",
        });
        return;
    }

    frappe.dom.freeze(__("Verifying TIN with URA…"));

    frappe.call({
        method:  "efris_integration.api.customer.verify_tin",
        args:    { tin: tin, company: frm.doc.company },
        callback(r) {
            frappe.dom.unfreeze();
            if (r.exc) return;          // server-side error already shown

            const d = r.message || {};
            if (d.status === "ok") {
                frappe.msgprint({
                    title:     __("TIN Verified"),
                    message:   `
                        <b>TIN:</b> ${d.tin || tin}<br>
                        <b>Name:</b> ${d.taxpayer_name || "—"}<br>
                        <b>Status:</b> ${d.status_desc  || "Active"}
                    `,
                    indicator: "green",
                });
                // Auto-fill invoice name if returned
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
