/**
 * EFRIS toolbar for Sales Order.
 * Mirrors: SalesOrder.cs, UraSalesOrder.cs → T109
 */

frappe.ui.form.on("Sales Order", {

    refresh(frm) {
        efris_add_status_badge(frm);

        if (frm.doc.docstatus === 1 && !frm.doc.efris_invoice_id) {
            frm.add_custom_button(__("Submit to EFRIS"), () => {
                efris_confirm_and_call(frm, __("Submit Sales Order to EFRIS"), () => {
                    efris_call(frm,
                        "efris_integration.api.sales_order.submit_sales_order",
                        { order_name: frm.doc.name }
                    );
                });
            }, __("EFRIS"));
        }

        if (frm.doc.docstatus === 1) {
            frm.add_custom_button(__("Recalculate Excise"), () => {
                frappe.call({
                    method: "efris_integration.excise.recalculate_excise",
                    args: { doctype: "Sales Order", docname: frm.doc.name },
                    callback(r) { if (r.message) frm.reload_doc(); }
                });
            }, __("EFRIS"));

            frm.add_custom_button(__("EFRIS Log"), () => efris_open_log(frm), __("EFRIS"));
        }
    }
});
