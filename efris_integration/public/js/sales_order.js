/**
 * EFRIS toolbar for Sales Order.
 * Sales Orders are not sent to URA (the Sales Invoice is the fiscal document);
 * only the excise recalculation lives here. Mirrors: SalesOrder.cs
 */

frappe.ui.form.on("Sales Order", {

    refresh(frm) {
        if (frm.doc.docstatus === 1) {
            frm.add_custom_button(__("Recalculate Excise"), () => {
                frappe.call({
                    method: "efris_integration.excise.recalculate_excise",
                    args: { doctype: "Sales Order", docname: frm.doc.name },
                    callback(r) { if (r.message) frm.reload_doc(); }
                });
            }, __("EFRIS"));
        }
    }
});
