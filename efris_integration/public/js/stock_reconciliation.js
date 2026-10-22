/**
 * EFRIS toolbar for Stock Reconciliation (SAP B1 Inventory Posting).
 * Opening Stock goes to URA as stock in; a count is sent as the difference
 * per line (stock in for increases, stock out for decreases). See api/stock.py.
 */

frappe.ui.form.on("Stock Reconciliation", {

    refresh(frm) {
        efris_add_status_badge(frm);

        if (frm.doc.docstatus !== 1) return;

        if (frm.doc.efris_status !== "Submitted") {
            frm.add_custom_button(__("Upload to EFRIS"), () => {
                const what = frm.doc.purpose === "Opening Stock"
                    ? __("as opening stock")
                    : __("as the counted differences");
                frappe.confirm(
                    __("Upload <b>{0}</b> to URA EFRIS {1}?", [frm.doc.name, what]),
                    () => efris_call(frm,
                        "efris_integration.api.stock.submit_stock_reconciliation",
                        { reconciliation_name: frm.doc.name }
                    )
                );
            }, __("EFRIS"));
        }
        frm.add_custom_button(__("EFRIS Log"), () => efris_open_log(frm), __("EFRIS"));
    }
});
