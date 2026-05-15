/**
 * EFRIS toolbar for Sales Invoice.
 *
 * Mirrors: ARInvoice.cs, UraARInvoice.cs, ARCreditNote.cs
 *
 * Buttons added:
 *   - Submit to EFRIS     (T108 for normal invoices, T110 for credit notes)
 *   - Recalculate Excise  (for excisable goods companies)
 *   - Query EFRIS         (T106)
 *   - View EFRIS Log
 */

frappe.ui.form.on("Sales Invoice", {

    refresh(frm) {
        efris_add_status_badge(frm);

        if (frm.doc.docstatus === 1) {
            // ── Primary submit button ──────────────────────────────────
            if (!frm.doc.efris_invoice_id) {
                const label = frm.doc.is_return
                    ? __("Submit Credit Note to EFRIS")
                    : __("Submit to EFRIS");

                frm.add_custom_button(label, () => {
                    efris_confirm_and_call(frm, label, () => {
                        const method = frm.doc.is_return
                            ? "efris_integration.api.credit_note.submit_credit_note"
                            : "efris_integration.api.invoice.submit_sales_invoice";

                        if (frm.doc.is_return) {
                            efris_credit_note_dialog(frm, method);
                        } else {
                            efris_call(frm, method, { invoice_name: frm.doc.name });
                        }
                    });
                }, __("EFRIS"));
            } else {
                // Already submitted — show re-query option
                frm.add_custom_button(__("Refresh from EFRIS"), () => {
                    efris_call(frm, "efris_integration.api.invoice.query_invoices",
                        { invoice_no: frm.doc.name }, false);
                }, __("EFRIS"));
            }

            // ── Excise recalculate ─────────────────────────────────────
            frm.add_custom_button(__("Recalculate Excise"), () => {
                frappe.call({
                    method: "efris_integration.excise.recalculate_excise",
                    args: { doctype: "Sales Invoice", docname: frm.doc.name },
                    callback(r) {
                        if (r.message) {
                            frm.reload_doc();
                        }
                    }
                });
            }, __("EFRIS"));
        }

        // ── QR Code viewer ─────────────────────────────────────────────
        if (frm.doc.efris_qr_code) {
            frm.add_custom_button(__("View QR Code"), () => {
                efris_show_qr(frm);
            }, __("EFRIS"));
        }

        // ── View log (always visible on submitted docs) ────────────────
        if (frm.doc.docstatus >= 1) {
            frm.add_custom_button(__("EFRIS Log"), () => {
                efris_open_log(frm);
            }, __("EFRIS"));
        }
    },

    // Auto-populate buyer type from customer EFRIS fields
    customer(frm) {
        if (!frm.doc.customer) return;
        frappe.db.get_value("Customer", frm.doc.customer,
            ["efris_buyer_type", "efris_nin_brn", "tax_id"],
            (r) => {
                if (r && r.efris_buyer_type) {
                    frm.set_value("efris_buyer_type_display", r.efris_buyer_type);
                }
            }
        );
    }
});

// ─── Credit Note dialog (reason code picker) ──────────────────────────────
function efris_credit_note_dialog(frm, method) {
    const d = new frappe.ui.Dialog({
        title: __("Submit Credit Note to EFRIS"),
        fields: [
            {
                label: __("Reason Code"),
                fieldname: "reason_code",
                fieldtype: "Select",
                options: [
                    { value: "101", label: "101 – Sales Return" },
                    { value: "102", label: "102 – Cancellation" },
                    { value: "103", label: "103 – Pricing Error" },
                    { value: "104", label: "104 – Duplicate Invoice" },
                    { value: "105", label: "105 – Other" },
                ],
                reqd: 1,
                default: "101",
            },
            {
                label: __("Reason Description"),
                fieldname: "reason",
                fieldtype: "Small Text",
            },
        ],
        primary_action_label: __("Submit"),
        primary_action(values) {
            d.hide();
            efris_call(frm, method, {
                invoice_name: frm.doc.name,
                reason_code: values.reason_code,
                reason: values.reason || "",
            });
        }
    });
    d.show();
}
