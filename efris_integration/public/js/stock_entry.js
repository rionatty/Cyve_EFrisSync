/**
 * EFRIS toolbar for Stock Entry.
 *
 * One "Upload to EFRIS" button; the server decides what URA gets
 * (see api/stock.py):
 *   Material Receipt                  -> stock in   (Stock In Type, Supplier)
 *   Material Issue / Send to Subcon.  -> stock out  (Adjust Type, Reason)
 *   Manufacture / Repack              -> stock in for produced lines and/or
 *                                        stock out for consumed lines (EFRIS Settings)
 *   Material Transfer                 -> branch-to-branch transfer
 * The fields it needs are on the URA tab and can be edited after submit.
 *
 * Mirrors: GoodsReceipt.cs, GoodsIssue.cs, IssueFromProduction.cs,
 *          ReceiptFromProduction.cs, InventoryTransfer.cs
 */

frappe.ui.form.on("Stock Entry", {

    refresh(frm) {
        efris_add_status_badge(frm);

        if (frm.doc.docstatus !== 1) return;

        if (frm.doc.efris_status !== "Submitted"
                && frm.doc.stock_entry_type !== "Material Transfer for Manufacture") {
            frm.add_custom_button(__("Upload to EFRIS"), () => efris_upload_stock_entry(frm), __("EFRIS"));
        }
        frm.add_custom_button(__("EFRIS Log"), () => efris_open_log(frm), __("EFRIS"));
    }
});


function efris_upload_stock_entry(frm) {
    const t = frm.doc.stock_entry_type;
    const missing = (label) => frappe.msgprint({
        title: __("EFRIS"),
        message: __("Set <b>{0}</b> on the URA tab first.", [label]),
        indicator: "orange",
    });

    let summary = "";
    if (t === "Material Receipt") {
        const type = frm.doc.efris_stock_in_type;
        if (!type) return missing(__("Stock In Type"));
        if (type !== "Manufacture/Assembling" && !frm.doc.efris_supplier_name) {
            return missing(__("Supplier Name"));
        }
        summary = __("Stock in: {0}<br>Supplier: {1}", [type, frm.doc.efris_supplier_name || "-"]);
    } else if (["Material Issue", "Send to Subcontractor"].includes(t)) {
        const adjust = frm.doc.efris_adjust_type;
        if (!adjust) return missing(__("Adjust Type"));
        if (adjust === "Others" && !frm.doc.efris_adjust_reason) return missing(__("Reason"));
        summary = __("Stock out: {0}", [adjust]);
    } else if (t === "Material Transfer") {
        summary = __("Transfer between URA branches: {0}", [frm.doc.efris_transfer_type || __("Others")]);
    } else {
        summary = __("Production: produced and consumed lines as set in EFRIS Settings");
    }

    efris_confirm_and_call(frm,
        __("Upload <b>{0}</b> to URA EFRIS?<br><br>{1}", [frm.doc.name, summary]),
        () => efris_call(frm,
            "efris_integration.api.stock.submit_stock_entry",
            { stock_entry_name: frm.doc.name }
        )
    );
}
