/**
 * EFRIS toolbar for Purchase Receipt (SAP B1 GRPO / Goods Return).
 * A receipt goes to URA as stock in, a purchase return as stock out
 * (see api/stock.py). Stock-In Type and Supplier TIN are on the EFRIS tab.
 */

frappe.ui.form.on("Purchase Receipt", {

    refresh(frm) {
        efris_add_status_badge(frm);

        if (frm.doc.docstatus !== 1) return;

        if (frm.doc.efris_status !== "Submitted") {
            frm.add_custom_button(__("Upload to EFRIS"), () => {
                const what = frm.doc.is_return
                    ? __("as a stock out (return to supplier)")
                    : __("as a stock in: {0}", [frm.doc.efris_stock_in_option || __("Local Purchase")]);
                efris_confirm_and_call(frm,
                    __("Upload <b>{0}</b> to URA EFRIS {1}?", [frm.doc.name, what]),
                    () => efris_call(frm,
                        "efris_integration.api.stock.submit_purchase_receipt",
                        { receipt_name: frm.doc.name }
                    )
                );
            }, __("EFRIS"));
        }
        frm.add_custom_button(__("EFRIS Log"), () => efris_open_log(frm), __("EFRIS"));
    }
});
