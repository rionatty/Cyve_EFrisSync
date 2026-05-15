/**
 * EFRIS toolbar for Delivery Note.
 * Mirrors: Delivery.cs → T109
 */

frappe.ui.form.on("Delivery Note", {

    refresh(frm) {
        efris_add_status_badge(frm);

        if (frm.doc.docstatus === 1 && !frm.doc.efris_invoice_id) {
            frm.add_custom_button(__("Submit to EFRIS"), () => {
                efris_confirm_and_call(frm, __("Submit Delivery Note to EFRIS"), () => {
                    efris_call(frm,
                        "efris_integration.api.sales_order.submit_delivery_note",
                        { delivery_name: frm.doc.name }
                    );
                });
            }, __("EFRIS"));
        }

        if (frm.doc.docstatus >= 1) {
            frm.add_custom_button(__("EFRIS Log"), () => efris_open_log(frm), __("EFRIS"));
        }
    }
});
