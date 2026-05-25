/**
 * EFRIS toolbar for Stock Entry.
 *
 * Entry-type → EFRIS mapping (mirrors SAP B1):
 * ─────────────────────────────────────────────────────────────────────────
 * Material Receipt                   → T114  (Goods Receipt / Stock In)
 * Material Issue                     → T115  (Goods Issue / Stock Out)
 *   Normal Issue (default)           →   operationType 201
 *   Out of Stock Adjust              →   operationType 206
 *   Error Adjust                     →   operationType 207
 *   Others                           →   operationType 208
 * Material Transfer for Manufacture  → T115  (Issue From Production, op 202)
 * Manufacture                        → T114  (Receipt From Production, FG lines)
 * Send to Subcontractor              → T115  (Goods Issue, op 201)
 * Material Transfer (same branch)    → Skip (internal; no EFRIS required)
 * Material Transfer (diff branches)  → T115 op 203 (issue) + T114 op 105 (receipt)
 *
 * T131 (Goods Stock Maintain) is available on all submitted entries and uses a
 * separate duplicate guard (efris_ura_remark) — independent of T114/T115.
 * ─────────────────────────────────────────────────────────────────────────
 *
 * Mirrors: GoodsReceipt.cs, GoodsIssue.cs, GoodsReturn.cs,
 *          IssueFromProduction.cs, ReceiptFromProduction.cs,
 *          InventoryTransfer.cs
 */

/* global frappe, __ */

frappe.ui.form.on("Stock Entry", {

    refresh(frm) {
        efris_add_status_badge(frm);

        if (frm.doc.docstatus !== 1) return;

        var t = frm.doc.stock_entry_type;

        // ── Submission guards ────────────────────────────────────────────────
        // T114/T115/T119 use efris_invoice_id as their submitted indicator.
        // T131 uses efris_ura_remark (Stock-Entry-only field, never copied).
        // These two channels are independent — both buttons can appear at once.
        var doneT114T115 = !!(frm.doc.efris_invoice_id);
        var doneT131     = !!(frm.doc.efris_ura_remark
                              && frm.doc.efris_status === "Submitted");

        // ── T131 — Goods Stock Maintain ──────────────────────────────────────
        // Always available unless already T131-submitted.
        // Reads Stock In Type / Supplier Name / Reference No from the URA tab
        // (allow_on_submit fields) — no dialog needed.
        if (!doneT131) {
            frm.add_custom_button(__("Upload to EFRIS (T131 – Stock In)"), function () {
                efris_upload_stock_in(frm);
            }, __("EFRIS"));
        }

        // ── T114 / T115 / T119 buttons ───────────────────────────────────────
        // Hidden once efris_invoice_id is set (already submitted via these APIs).
        if (!doneT114T115) {

            // T114 — Material Receipt
            if (t === "Material Receipt") {
                frm.add_custom_button(__("Submit to EFRIS (T114 – Stock In)"), function () {
                    efris_stock_entry_dialog(frm, "receipt");
                }, __("EFRIS"));
            }

            // T115 — Material Issue / Send to Subcontractor
            // Shows a dialog with EFRIS Option (Normal/Adjust/Error/Others) + Reason.
            if (t === "Material Issue" || t === "Send to Subcontractor") {
                frm.add_custom_button(__("Submit to EFRIS (T115 – Stock Out)"), function () {
                    efris_issue_dialog(frm);
                }, __("EFRIS"));
            }

            // T115 op 202 — Issue From Production
            if (t === "Material Transfer for Manufacture") {
                frm.add_custom_button(__("Submit to EFRIS (T115 – Issue From Production)"), function () {
                    efris_confirm_and_call(
                        frm,
                        __("Submit Issue From Production to EFRIS"),
                        function () {
                            efris_call(frm,
                                "efris_integration.api.stock.submit_stock_issue",
                                { stock_entry_name: frm.doc.name }
                            );
                        }
                    );
                }, __("EFRIS"));
            }

            // T114 op 103 — Receipt From Production (Manufacture FG lines)
            if (t === "Manufacture") {
                frm.add_custom_button(__("Submit to EFRIS (T114 – Receipt From Production)"), function () {
                    efris_confirm_and_call(
                        frm,
                        __("Submit Receipt From Production to EFRIS"),
                        function () {
                            efris_call(frm,
                                "efris_integration.api.stock.submit_stock_receipt",
                                { stock_entry_name: frm.doc.name }
                            );
                        }
                    );
                }, __("EFRIS"));
            }

            // T119 — Goods Return (only when efris_return_against_invoice_id is set)
            if (frm.doc.add_to_transit === 0
                    && t === "Material Issue"
                    && frm.doc.efris_return_against_invoice_id) {
                frm.add_custom_button(__("Submit Return to EFRIS (T119)"), function () {
                    efris_return_dialog(frm);
                }, __("EFRIS"));
            }

            // Material Transfer — server auto-detects same vs inter-branch.
            // Same branch → informational skip message (no EFRIS required).
            // Different branches → T115 op 203 (issue) + T114 op 105 (receipt).
            if (t === "Material Transfer") {
                frm.add_custom_button(__("Submit Transfer to EFRIS"), function () {
                    efris_transfer_dialog(frm);
                }, __("EFRIS"));
            }
        }

        frm.add_custom_button(__("EFRIS Log"), function () {
            efris_open_log(frm);
        }, __("EFRIS"));
    }
});


// ─── T131 Upload ──────────────────────────────────────────────────────────────

/**
 * T131 — Upload to EFRIS (Goods Stock Maintain).
 *
 * Reads Stock In Type, Supplier Name, Reference No directly from the URA tab
 * (all three have allow_on_submit=1 so they're editable on a submitted doc).
 * Validates required fields client-side before calling the server.
 */
function efris_upload_stock_in(frm) {
    var stockInType  = (frm.doc.efris_stock_in_type  || "").trim();
    var supplierName = (frm.doc.efris_supplier_name  || "").trim();
    var referenceNo  = (frm.doc.efris_reference_no   || "").trim();

    if (!stockInType) {
        frappe.msgprint({
            title:     __("Stock In Type Required"),
            message:   __("Please set <b>Stock In Type</b> on the URA tab before uploading."),
            indicator: "orange",
        });
        return;
    }

    var isMfg = stockInType === "Manufacture/Assembling";
    if (!isMfg && !supplierName) {
        frappe.msgprint({
            title:     __("Supplier Name Required"),
            message:   __("Please set <b>Supplier Name</b> on the URA tab.<br>"
                        + "(Only optional for Manufacture/Assembling.)"),
            indicator: "orange",
        });
        return;
    }

    var refLabel = referenceNo || frm.doc.name;
    frappe.confirm(
        __("Upload <b>" + frm.doc.name + "</b> to URA EFRIS as Goods Stock Maintain (T131)?<br><br>"
         + "<b>Stock In Type:</b> " + stockInType + "<br>"
         + "<b>Supplier Name:</b> " + (supplierName || "(none)") + "<br>"
         + "<b>Reference No:</b> "  + refLabel),
        function () {
            efris_call(frm,
                "efris_integration.api.stock_in.upload_stock_in",
                { stock_entry_name: frm.doc.name, company: frm.doc.company }
            );
        }
    );
}


// ─── T114 Receipt Dialog ──────────────────────────────────────────────────────

function efris_stock_entry_dialog(frm, mode) {
    var stockInOptions = [
        { value: "101", label: "101 – Import" },
        { value: "102", label: "102 – Local Purchase" },
        { value: "103", label: "103 – Manufacture" },
        { value: "104", label: "104 – Opening Stock" },
        { value: "105", label: "105 – Transfer" },
    ];

    var d = new frappe.ui.Dialog({
        title: mode === "receipt"
            ? __("EFRIS Stock-In Details (T114)")
            : __("EFRIS Stock-Out Details"),
        fields: [
            ...(mode === "receipt" ? [{
                label:     __("Stock-In Type"),
                fieldname: "efris_stock_in_option",
                fieldtype: "Select",
                options:   stockInOptions,
                default:   frm.doc.efris_stock_in_option || "102",
                reqd:      1,
            }] : []),
            {
                label:     __("Supplier / Recipient Name"),
                fieldname: "efris_supplier_name",
                fieldtype: "Data",
                default:   frm.doc.efris_supplier_name || "",
            },
            {
                label:     __("Supplier / Recipient TIN"),
                fieldname: "efris_supplier_tin",
                fieldtype: "Data",
                default:   frm.doc.efris_supplier_tin || "",
            },
            {
                label:     __("Reference No"),
                fieldname: "efris_reference_no",
                fieldtype: "Data",
                default:   frm.doc.efris_reference_no || frm.doc.name,
            },
        ],
        primary_action_label: __("Submit to EFRIS"),
        primary_action(values) {
            d.hide();
            var updates = {
                efris_supplier_name: values.efris_supplier_name || "",
                efris_supplier_tin:  values.efris_supplier_tin  || "",
                efris_reference_no:  values.efris_reference_no  || "",
            };
            if (values.efris_stock_in_option) {
                updates.efris_stock_in_option = values.efris_stock_in_option;
            }
            frappe.db.set_value("Stock Entry", frm.doc.name, updates).then(function () {
                efris_call(frm,
                    "efris_integration.api.stock.submit_stock_receipt",
                    { stock_entry_name: frm.doc.name }
                );
            });
        },
    });
    d.show();
}


// ─── T115 Issue Dialog ────────────────────────────────────────────────────────

/**
 * T115 — Goods Issue dialog.
 *
 * Mirrors SAP B1 EFRIS tab "EFRIS Option" dropdown:
 *   Normal Issue        → operationType 201
 *   Out of Stock Adjust → operationType 206
 *   Error Adjust        → operationType 207
 *   Others              → operationType 208
 *
 * The "Reason" field is shown and required for adjustment types.
 */
function efris_issue_dialog(frm) {
    var ISSUE_OPTIONS = [
        { value: "Normal Issue",        label: __("Normal Issue (201)") },
        { value: "Out of Stock Adjust", label: __("Out of Stock Adjust (206)") },
        { value: "Error Adjust",        label: __("Error Adjust (207)") },
        { value: "Others",              label: __("Others (208)") },
    ];

    function isAdjust(v) {
        return v === "Out of Stock Adjust"
            || v === "Error Adjust"
            || v === "Others";
    }

    var initType        = frm.doc.efris_issue_type || "Normal Issue";
    var initNeedsReason = isAdjust(initType);

    var d = new frappe.ui.Dialog({
        title: __("EFRIS Goods Issue (T115)"),
        fields: [
            {
                label:     __("EFRIS Issue Type"),
                fieldname: "efris_issue_type",
                fieldtype: "Select",
                options:   ISSUE_OPTIONS,
                default:   initType,
                reqd:      1,
                onchange: function () {
                    var v = d.get_value("efris_issue_type");
                    var needsReason = isAdjust(v);
                    d.set_df_property("efris_adjust_reason", "reqd",   needsReason ? 1 : 0);
                    d.set_df_property("efris_adjust_reason", "hidden", needsReason ? 0 : 1);
                    d.refresh_field("efris_adjust_reason");
                },
            },
            {
                label:       __("Reason"),
                fieldname:   "efris_adjust_reason",
                fieldtype:   "Small Text",
                default:     frm.doc.efris_adjust_reason || "",
                description: __("Required for adjustment types. Sent to URA as the transaction remarks."),
                hidden:      initNeedsReason ? 0 : 1,
                reqd:        initNeedsReason ? 1 : 0,
            },
            {
                label:     __("Recipient Name"),
                fieldname: "efris_recipient_name",
                fieldtype: "Data",
                default:   frm.doc.efris_recipient_name || "",
            },
            {
                label:     __("Recipient TIN"),
                fieldname: "efris_recipient_tin",
                fieldtype: "Data",
                default:   frm.doc.efris_recipient_tin || "",
            },
        ],
        primary_action_label: __("Submit to EFRIS"),
        primary_action: function (values) {
            d.hide();
            frappe.db.set_value("Stock Entry", frm.doc.name, {
                efris_issue_type:     values.efris_issue_type     || "Normal Issue",
                efris_adjust_reason:  values.efris_adjust_reason  || "",
                efris_recipient_name: values.efris_recipient_name || "",
                efris_recipient_tin:  values.efris_recipient_tin  || "",
            }).then(function () {
                efris_call(frm,
                    "efris_integration.api.stock.submit_stock_issue",
                    { stock_entry_name: frm.doc.name }
                );
            });
        },
    });
    d.show();
}


// ─── Material Transfer Dialog ─────────────────────────────────────────────────

/**
 * Material Transfer → EFRIS.
 *
 * The server automatically detects whether this is a same-branch or inter-branch
 * transfer by comparing the EFRIS Branch IDs on the source and target warehouses:
 *
 *   Same branch   → No EFRIS submission (informational message returned).
 *   Diff branches → T115 op 203 (issue from source) + T114 op 105 (receipt at target).
 *
 * Branch validation (efris_branch_id must be set on warehouses) is enforced
 * server-side when multi-branch mode is enabled in EFRIS Settings.
 */
function efris_transfer_dialog(frm) {
    frappe.confirm(
        __("Submit Material Transfer <b>" + frm.doc.name + "</b> to EFRIS?<br><br>"
         + "The system will automatically detect if this is an inter-branch transfer "
         + "(different EFRIS Branch IDs on source and target warehouses) and submit "
         + "T115 (Stock Out) + T114 (Stock In) accordingly.<br><br>"
         + "<b>Same branch:</b> No EFRIS submission required — transfer will be skipped.<br>"
         + "<b>Different branches:</b> Both T115 and T114 will be submitted to URA."),
        function () {
            efris_call(frm,
                "efris_integration.api.inventory_transfer.submit_inter_branch_transfer",
                { stock_entry_name: frm.doc.name }
            );
        }
    );
}


// ─── T119 Goods Return Dialog ─────────────────────────────────────────────────

function efris_return_dialog(frm) {
    var d = new frappe.ui.Dialog({
        title: __("EFRIS Goods Return (T119)"),
        fields: [
            {
                label:     __("Original EFRIS Invoice ID"),
                fieldname: "efris_return_against_invoice_id",
                fieldtype: "Data",
                default:   frm.doc.efris_return_against_invoice_id || "",
                reqd:      1,
            },
            {
                label:    __("Return Reason Code"),
                fieldname: "efris_return_reason_code",
                fieldtype: "Select",
                options:  [
                    { value: "101", label: "101 – Goods Damaged" },
                    { value: "102", label: "102 – Wrong Item" },
                    { value: "103", label: "103 – Overstocking" },
                    { value: "104", label: "104 – Expired" },
                    { value: "105", label: "105 – Other" },
                ],
                default:  frm.doc.efris_return_reason_code || "101",
                reqd:     1,
            },
            {
                label:     __("Return Reason"),
                fieldname: "efris_return_reason",
                fieldtype: "Small Text",
                default:   frm.doc.efris_return_reason || "",
            },
        ],
        primary_action_label: __("Submit Return to EFRIS"),
        primary_action: function (values) {
            d.hide();
            frappe.db.set_value("Stock Entry", frm.doc.name, {
                efris_return_against_invoice_id: values.efris_return_against_invoice_id,
                efris_return_reason_code:        values.efris_return_reason_code,
                efris_return_reason:             values.efris_return_reason || "",
            }).then(function () {
                efris_call(frm,
                    "efris_integration.api.stock.submit_goods_return",
                    { doctype: "Stock Entry", docname: frm.doc.name }
                );
            });
        },
    });
    d.show();
}
