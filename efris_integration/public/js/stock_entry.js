/**
 * EFRIS toolbar for Stock Entry.
 *
 * Entry-type → EFRIS mapping (mirrors SAP B1):
 * ─────────────────────────────────────────────────────────────────────────
 * Material Receipt                   → T114  (Goods Receipt / Stock In)
 * Material Issue                     → T115  (Goods Issue / Stock Out)
 * Material Transfer for Manufacture  → T115  (Issue From Production)
 * Manufacture                        → T114  (Receipt From Production, FG lines)
 *                                      + T115 (consumed RM lines, if enabled)
 * Send to Subcontractor              → T115  (Goods Issue)
 * Material Transfer                  → T115  (only if inter-branch, configurable)
 * ─────────────────────────────────────────────────────────────────────────
 *
 * Mirrors: GoodsReceipt.cs, GoodsIssue.cs, GoodsReturn.cs,
 *          IssueFromProduction.cs, ReceiptFromProduction.cs
 */

frappe.ui.form.on("Stock Entry", {

    refresh(frm) {
        efris_add_status_badge(frm);

        if (frm.doc.docstatus !== 1) return;
        if (frm.doc.efris_invoice_id) {
            // Already submitted; only show log
            frm.add_custom_button(__("EFRIS Log"), () => efris_open_log(frm), __("EFRIS"));
            return;
        }

        const t = frm.doc.stock_entry_type;

        // ── T114 types ───────────────────────────────────────────────────
        if (t === "Material Receipt") {
            frm.add_custom_button(__("Submit to EFRIS (T114 – Stock In)"), () => {
                efris_stock_entry_dialog(frm, "receipt");
            }, __("EFRIS"));
        }

        // ── T115 types ───────────────────────────────────────────────────
        if (["Material Issue", "Send to Subcontractor"].includes(t)) {
            frm.add_custom_button(__("Submit to EFRIS (T115 – Stock Out)"), () => {
                efris_confirm_and_call(frm, __("Submit Goods Issue to EFRIS"), () => {
                    efris_call(frm,
                        "efris_integration.api.stock.submit_stock_issue",
                        { stock_entry_name: frm.doc.name }
                    );
                });
            }, __("EFRIS"));
        }

        // ── Issue From Production ────────────────────────────────────────
        if (t === "Material Transfer for Manufacture") {
            frm.add_custom_button(__("Submit to EFRIS (T115 – Issue From Production)"), () => {
                efris_confirm_and_call(frm, __("Submit Issue From Production to EFRIS"), () => {
                    efris_call(frm,
                        "efris_integration.api.stock.submit_stock_issue",
                        { stock_entry_name: frm.doc.name }
                    );
                });
            }, __("EFRIS"));
        }

        // ── Receipt From Production (Manufacture) ────────────────────────
        if (t === "Manufacture") {
            frm.add_custom_button(__("Submit to EFRIS (T114 – Receipt From Production)"), () => {
                efris_confirm_and_call(frm, __("Submit Receipt From Production to EFRIS"), () => {
                    efris_call(frm,
                        "efris_integration.api.stock.submit_stock_receipt",
                        { stock_entry_name: frm.doc.name }
                    );
                });
            }, __("EFRIS"));
        }

        // ── Goods Return ─────────────────────────────────────────────────
        if (frm.doc.add_to_transit === 0 && t === "Material Issue"
                && frm.doc.efris_return_against_invoice_id) {
            frm.add_custom_button(__("Submit Return to EFRIS (T119)"), () => {
                efris_return_dialog(frm);
            }, __("EFRIS"));
        }

        // ── Inter-Branch Transfer ────────────────────────────────────
        if (t === "Material Transfer") {
            frm.add_custom_button(__("Submit Inter-Branch Transfer to EFRIS"), () => {
                efris_confirm_and_call(frm, __("Submit Inter-Branch Transfer to EFRIS"), () => {
                    efris_call(frm,
                        "efris_integration.api.inventory_transfer.submit_inter_branch_transfer",
                        { stock_entry_name: frm.doc.name }
                    );
                });
            }, __("EFRIS"));
        }

        frm.add_custom_button(__("EFRIS Log"), () => efris_open_log(frm), __("EFRIS"));
    }
});


// ─── Dialogs ──────────────────────────────────────────────────────────────

function efris_stock_entry_dialog(frm, mode) {
    const stockInOptions = [
        { value: "101", label: "101 – Import" },
        { value: "102", label: "102 – Local Purchase" },
        { value: "103", label: "103 – Manufacture" },
        { value: "104", label: "104 – Opening Stock" },
        { value: "105", label: "105 – Transfer" },
    ];

    const d = new frappe.ui.Dialog({
        title: mode === "receipt"
            ? __("EFRIS Stock-In Details")
            : __("EFRIS Stock-Out Details"),
        fields: [
            ...(mode === "receipt" ? [{
                label: __("Stock-In Type"),
                fieldname: "efris_stock_in_option",
                fieldtype: "Select",
                options: stockInOptions,
                default: frm.doc.efris_stock_in_option || "102",
                reqd: 1,
            }] : []),
            {
                label: __("Supplier / Recipient Name"),
                fieldname: "efris_supplier_name",
                fieldtype: "Data",
                default: frm.doc.efris_supplier_name || "",
            },
            {
                label: __("Supplier / Recipient TIN"),
                fieldname: "efris_supplier_tin",
                fieldtype: "Data",
                default: frm.doc.efris_supplier_tin || "",
            },
            {
                label: __("Reference No"),
                fieldname: "efris_reference_no",
                fieldtype: "Data",
                default: frm.doc.efris_reference_no || frm.doc.name,
            },
        ],
        primary_action_label: __("Submit to EFRIS"),
        primary_action(values) {
            d.hide();
            const updates = {
                efris_supplier_name: values.efris_supplier_name,
                efris_supplier_tin: values.efris_supplier_tin,
                efris_reference_no: values.efris_reference_no,
            };
            if (values.efris_stock_in_option) {
                updates.efris_stock_in_option = values.efris_stock_in_option;
            }
            frappe.db.set_value("Stock Entry", frm.doc.name, updates).then(() => {
                efris_call(frm,
                    "efris_integration.api.stock.submit_stock_receipt",
                    { stock_entry_name: frm.doc.name }
                );
            });
        }
    });
    d.show();
}


function efris_return_dialog(frm) {
    const d = new frappe.ui.Dialog({
        title: __("EFRIS Goods Return (T119)"),
        fields: [
            {
                label: __("Original EFRIS Invoice ID"),
                fieldname: "efris_return_against_invoice_id",
                fieldtype: "Data",
                default: frm.doc.efris_return_against_invoice_id || "",
                reqd: 1,
            },
            {
                label: __("Return Reason Code"),
                fieldname: "efris_return_reason_code",
                fieldtype: "Select",
                options: [
                    { value: "101", label: "101 – Goods Damaged" },
                    { value: "102", label: "102 – Wrong Item" },
                    { value: "103", label: "103 – Overstocking" },
                    { value: "104", label: "104 – Expired" },
                    { value: "105", label: "105 – Other" },
                ],
                default: frm.doc.efris_return_reason_code || "101",
                reqd: 1,
            },
            {
                label: __("Return Reason"),
                fieldname: "efris_return_reason",
                fieldtype: "Small Text",
                default: frm.doc.efris_return_reason || "",
            },
        ],
        primary_action_label: __("Submit Return to EFRIS"),
        primary_action(values) {
            d.hide();
            frappe.db.set_value("Stock Entry", frm.doc.name, {
                efris_return_against_invoice_id: values.efris_return_against_invoice_id,
                efris_return_reason_code: values.efris_return_reason_code,
                efris_return_reason: values.efris_return_reason,
            }).then(() => {
                efris_call(frm,
                    "efris_integration.api.stock.submit_goods_return",
                    { doctype: "Stock Entry", docname: frm.doc.name }
                );
            });
        }
    });
    d.show();
}
