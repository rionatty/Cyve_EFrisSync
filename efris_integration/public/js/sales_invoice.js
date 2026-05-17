/**
 * EFRIS Sales Invoice form enhancements.
 *
 * Button layout (mirrors SAP B1 AR Invoice toolbar):
 *   [Upload to EFRIS]  ← standalone PRIMARY button, always visible in toolbar
 *   [EFRIS ▾]          ← group dropdown for secondary actions
 *       Refresh from EFRIS
 *       Recalculate Excise
 *       View QR Code
 *       Verify on URA
 *       EFRIS Log
 *
 * Safety rules:
 *   - Entire body wrapped in try-catch so EFRIS errors never remove standard buttons
 *   - Every efris_utils.js function guarded with typeof check (bench build may not
 *     have run yet; calling an undefined function throws and kills all buttons)
 *   - frm.add_custom_button() return value stored before .addClass() (can be null)
 */

/* global frappe, __ */

frappe.ui.form.on("Sales Invoice", {

    refresh: function (frm) {
        try {
            _efris_refresh(frm);
        } catch (e) {
            console.error("EFRIS Sales Invoice refresh error (standard buttons are safe):", e);
        }
    }

});


function _efris_refresh(frm) {

    // Status badge — guard: efris_utils.js might not be built yet
    if (typeof efris_add_status_badge === "function") {
        efris_add_status_badge(frm);
    }

    // EFRIS buttons only make sense on submitted documents
    if (frm.doc.docstatus !== 1) return;

    // ────────────────────────────────────────────────────────────────────────
    // PRIMARY ACTION — standalone button, NOT inside any group/dropdown
    // This makes it immediately visible in the toolbar, same as SAP B1.
    // ────────────────────────────────────────────────────────────────────────
    if (!frm.doc.efris_invoice_id) {

        var is_return = !!frm.doc.is_return;
        var btn_label = is_return
            ? __("Upload Credit Note to EFRIS")
            : __("Upload to EFRIS");

        // No third argument → standalone button in toolbar (not in dropdown)
        var $primary = frm.add_custom_button(btn_label, function () {
            frappe.confirm(
                __("Submit {0} to URA EFRIS?", [frm.doc.name]),
                function () {
                    if (is_return) {
                        _credit_note_dialog(frm);
                    } else {
                        frappe.call({
                            method: "efris_integration.api.invoice.submit_sales_invoice",
                            args: { invoice_name: frm.doc.name },
                            freeze: true,
                            freeze_message: __("Submitting to EFRIS..."),
                            callback: function (r) {
                                if (!r.exc) {
                                    frappe.show_alert({
                                        message: __("Invoice submitted to EFRIS successfully"),
                                        indicator: "green"
                                    });
                                    frm.reload_doc();
                                }
                            }
                        });
                    }
                }
            );
        });

        // Make it a blue primary button
        if ($primary && typeof $primary.addClass === "function") {
            $primary.addClass("btn-primary");
        }

    }

    // ────────────────────────────────────────────────────────────────────────
    // SECONDARY ACTIONS — all inside the EFRIS dropdown group
    // ────────────────────────────────────────────────────────────────────────

    // Shown only after invoice is already on EFRIS
    if (frm.doc.efris_invoice_id) {
        frm.add_custom_button(__("Refresh from EFRIS"), function () {
            frappe.call({
                method: "efris_integration.api.invoice.query_invoices",
                args: { company: frm.doc.company, invoice_no: frm.doc.name },
                freeze: true,
                callback: function (r) {
                    if (!r.exc) frm.reload_doc();
                }
            });
        }, __("EFRIS"));
    }

    frm.add_custom_button(__("Recalculate Excise"), function () {
        frappe.call({
            method: "efris_integration.excise.recalculate_excise",
            args: { doctype: "Sales Invoice", docname: frm.doc.name },
            callback: function (r) { if (r.message) frm.reload_doc(); }
        });
    }, __("EFRIS"));

    if (frm.doc.efris_qr_code || frm.doc.efris_qr_path) {
        frm.add_custom_button(__("View QR Code"), function () {
            if (typeof efris_show_qr === "function") {
                efris_show_qr(frm);
            } else {
                frappe.msgprint(__("QR viewer not loaded. Run bench build."));
            }
        }, __("EFRIS"));
    }

    if (frm.doc.efris_qr_path) {
        frm.add_custom_button(__("Verify on URA"), function () {
            window.open(frm.doc.efris_qr_path, "_blank");
        }, __("EFRIS"));
    }

    frm.add_custom_button(__("EFRIS Log"), function () {
        if (typeof efris_open_log === "function") {
            efris_open_log(frm);
        } else {
            frappe.set_route("List", "EFRIS Log", {
                document_type: "Sales Invoice",
                document_name: frm.doc.name
            });
        }
    }, __("EFRIS"));
}


// ── Credit Note reason-code dialog ────────────────────────────────────────────

function _credit_note_dialog(frm) {
    var d = new frappe.ui.Dialog({
        title: __("Upload Credit Note to EFRIS"),
        fields: [
            {
                label: __("Reason Code"),
                fieldname: "reason_code",
                fieldtype: "Select",
                options: [
                    { value: "101", label: "101 - Sales Return" },
                    { value: "102", label: "102 - Cancellation" },
                    { value: "103", label: "103 - Pricing Error" },
                    { value: "104", label: "104 - Duplicate Invoice" },
                    { value: "105", label: "105 - Other" }
                ],
                reqd: 1,
                default: "101"
            },
            {
                label: __("Reason Description"),
                fieldname: "reason",
                fieldtype: "Small Text"
            }
        ],
        primary_action_label: __("Upload"),
        primary_action: function (values) {
            d.hide();
            frappe.call({
                method: "efris_integration.api.credit_note.submit_credit_note",
                args: {
                    invoice_name: frm.doc.name,
                    reason_code: values.reason_code,
                    reason: values.reason || ""
                },
                freeze: true,
                freeze_message: __("Submitting Credit Note to EFRIS..."),
                callback: function (r) {
                    if (!r.exc) {
                        frappe.show_alert({ message: __("Credit Note submitted to EFRIS"), indicator: "green" });
                        frm.reload_doc();
                    }
                }
            });
        }
    });
    d.show();
}
