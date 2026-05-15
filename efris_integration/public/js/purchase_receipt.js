/**
 * EFRIS toolbar for Purchase Receipt (≡ SAP B1 GRPO).
 * Mirrors: GRPO.cs → T114
 *
 * Stock-in option codes:
 *   101 = Import
 *   102 = Local Purchase  (default)
 *   103 = Manufacture
 *   104 = Opening Stock
 */

frappe.ui.form.on("Purchase Receipt", {

    refresh(frm) {
        efris_add_status_badge(frm);

        if (frm.doc.docstatus === 1 && !frm.doc.efris_invoice_id) {
            frm.add_custom_button(__("Submit to EFRIS (T114)"), () => {
                efris_stock_in_dialog(frm,
                    "efris_integration.api.stock.submit_purchase_receipt",
                    { receipt_name: frm.doc.name }
                );
            }, __("EFRIS"));
        }

        if (frm.doc.docstatus >= 1) {
            frm.add_custom_button(__("EFRIS Log"), () => efris_open_log(frm), __("EFRIS"));
        }
    }
});

/**
 * Popup to let user confirm / override stock-in option before submitting.
 */
function efris_stock_in_dialog(frm, method, base_args) {
    const d = new frappe.ui.Dialog({
        title: __("EFRIS Stock-In Details"),
        fields: [
            {
                label: __("Stock-In Type"),
                fieldname: "efris_stock_in_option",
                fieldtype: "Select",
                options: [
                    { value: "101", label: "101 – Import" },
                    { value: "102", label: "102 – Local Purchase" },
                    { value: "103", label: "103 – Manufacture" },
                    { value: "104", label: "104 – Opening Stock" },
                ],
                default: frm.doc.efris_stock_in_option || "102",
                reqd: 1,
            },
            {
                label: __("Supplier TIN (EFRIS)"),
                fieldname: "efris_supplier_tin",
                fieldtype: "Data",
                default: frm.doc.efris_supplier_tin || "",
            },
        ],
        primary_action_label: __("Submit to EFRIS"),
        primary_action(values) {
            d.hide();
            // Persist the choice back to the doc first
            frappe.db.set_value("Purchase Receipt", frm.doc.name, {
                efris_stock_in_option: values.efris_stock_in_option,
                efris_supplier_tin: values.efris_supplier_tin,
            }).then(() => {
                efris_call(frm, method, base_args);
            });
        }
    });
    d.show();
}
