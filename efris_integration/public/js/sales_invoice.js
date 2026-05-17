/**
 * EFRIS Sales Invoice form enhancements.
 *
 * Mirrors SAP B1: AR Invoice → EFRIS tab
 *   - "Upload to EFRIS" button (T108 for normal, T110 for credit note)
 *   - QR Code, QR Path, Verification Code, FDN Number, Invoice ID, Remark
 *   - Recalculate Excise, EFRIS Log shortcuts
 */

frappe.ui.form.on("Sales Invoice", {

    refresh(frm) {
        efris_add_status_badge(frm);

        if (frm.doc.docstatus !== 1) return;

        // ── Upload to EFRIS ────────────────────────────────────────────────
        if (!frm.doc.efris_invoice_id) {
            const is_return = frm.doc.is_return;
            const label     = is_return
                ? __("Upload Credit Note to EFRIS")
                : __("Upload to EFRIS");

            frm.add_custom_button(label, () => {
                frappe.confirm(
                    __("Upload <b>{0}</b> to URA EFRIS?", [frm.doc.name]),
                    () => {
                        if (is_return) {
                            _credit_note_dialog(frm);
                        } else {
                            efris_call(frm,
                                "efris_integration.api.invoice.submit_sales_invoice",
                                { invoice_name: frm.doc.name }
                            );
                        }
                    }
                );
            }, __("EFRIS"))
            .addClass("btn-primary");

        } else {
            // Already uploaded — refresh from URA
            frm.add_custom_button(__("Refresh from EFRIS"), () => {
                efris_call(frm,
                    "efris_integration.api.invoice.query_invoices",
                    { company: frm.doc.company, invoice_no: frm.doc.name },
                    false
                );
            }, __("EFRIS"));
        }

        // ── Recalculate Excise ─────────────────────────────────────────────
        frm.add_custom_button(__("Recalculate Excise"), () => {
            frappe.call({
                method: "efris_integration.excise.recalculate_excise",
                args:   { doctype: "Sales Invoice", docname: frm.doc.name },
                callback(r) { if (r.message) frm.reload_doc(); }
            });
        }, __("EFRIS"));

        // ── View QR Code ───────────────────────────────────────────────────
        // Show QR if we have a base64 image OR a validation URL to generate from
        if (frm.doc.efris_qr_code || frm.doc.efris_qr_path) {
            frm.add_custom_button(__("View QR Code"), () => {
                efris_show_qr(frm);
            }, __("EFRIS"));
        }

        // ── Verify on URA ──────────────────────────────────────────────────
        if (frm.doc.efris_qr_path) {
            frm.add_custom_button(__("Verify on URA"), () => {
                window.open(frm.doc.efris_qr_path, "_blank");
            }, __("EFRIS"));
        }

        // ── EFRIS Log ──────────────────────────────────────────────────────
        frm.add_custom_button(__("EFRIS Log"), () => {
            efris_open_log(frm);
        }, __("EFRIS"));
    },
});


// ─── Credit Note — reason code dialog ────────────────────────────────────────

function _credit_note_dialog(frm) {
    const d = new frappe.ui.Dialog({
        title: __("Upload Credit Note to EFRIS"),
        fields: [
            {
                label:     __("Reason Code"),
                fieldname: "reason_code",
                fieldtype: "Select",
                options: [
                    { value: "101", label: "101 – Sales Return" },
                    { value: "102", label: "102 – Cancellation" },
                    { value: "103", label: "103 – Pricing Error" },
                    { value: "104", label: "104 – Duplicate Invoice" },
                    { value: "105", label: "105 – Other" },
                ],
                reqd:    1,
                default: "101",
            },
            {
                label:     __("Reason Description"),
                fieldname: "reason",
                fieldtype: "Small Text",
            },
        ],
        primary_action_label: __("Upload"),
        primary_action(values) {
            d.hide();
            efris_call(frm,
                "efris_integration.api.credit_note.submit_credit_note",
                {
                    invoice_name: frm.doc.name,
                    reason_code:  values.reason_code,
                    reason:       values.reason || "",
                }
            );
        },
    });
    d.show();
}
