/**
 * EFRIS toolbar for Stock Reconciliation.
 * Equivalent to SAP B1 Inventory Posting → T114 with option 104 (Opening Stock).
 */

frappe.ui.form.on("Stock Reconciliation", {

    refresh(frm) {
        efris_add_status_badge(frm);

        if (frm.doc.docstatus === 1 && !frm.doc.efris_invoice_id) {
            frm.add_custom_button(__("Submit Opening Stock to EFRIS (T114)"), () => {
                efris_confirm_and_call(frm,
                    __("Submit this reconciliation as Opening Stock to EFRIS?"),
                    () => {
                        // The override handler fires on_submit automatically if enabled.
                        // This button is for manual / retry submission.
                        frappe.call({
                            method: "efris_integration.overrides.stock_reconciliation._submit_opening_stock",
                            args: { doc: frm.doc },
                            freeze: true,
                            freeze_message: __("Submitting to EFRIS…"),
                            callback(r) {
                                if (!r.exc) {
                                    frappe.show_alert({ message: __("Submitted to EFRIS"), indicator: "green" });
                                    frm.reload_doc();
                                }
                            }
                        });
                    }
                );
            }, __("EFRIS"));
        }

        if (frm.doc.docstatus >= 1) {
            frm.add_custom_button(__("EFRIS Log"), () => efris_open_log(frm), __("EFRIS"));
        }
    }
});
