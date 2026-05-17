/**
 * EFRIS Sales Invoice form enhancements.  v2.1
 *
 * Button layout (mirrors SAP B1 AR Invoice toolbar):
 *   [Upload to EFRIS]      <- standalone PRIMARY button (no efris_invoice_id yet)
 *   [EFRIS dropdown]
 *       Refresh from EFRIS <- fetches URA details (shown when Pending OR Submitted)
 *       Recalculate Excise
 *       View QR Code
 *       Verify on URA
 *       EFRIS Log
 *
 * Safety rules:
 *   - Entire body wrapped in try-catch so EFRIS errors never remove standard buttons
 *   - Every efris_utils.js function guarded with typeof check
 *   - frm.add_custom_button() return stored before .addClass() (can be null)
 *   - console.log("[EFRIS JS v2.1]") on every refresh so we can confirm the file is loaded
 */

/* global frappe, __ */

frappe.ui.form.on("Sales Invoice", {

    refresh: function (frm) {
        // Confirm this JS version is loaded — check browser console for this line
        console.log("[EFRIS JS v2.1] refresh fired — docstatus=" + frm.doc.docstatus
            + " efris_invoice_id=" + (frm.doc.efris_invoice_id || "(empty)")
            + " efris_status=" + (frm.doc.efris_status || "(none)"));
        try {
            _efris_refresh(frm);
        } catch (e) {
            console.error("[EFRIS JS v2.1] refresh error (standard buttons are safe):", e);
        }
    }

});


function _efris_refresh(frm) {

    // Status badge — guard: efris_utils.js needs bench build; may not be loaded yet
    if (typeof efris_add_status_badge === "function") {
        try { efris_add_status_badge(frm); } catch (e) { console.warn("EFRIS badge error:", e); }
    }

    // EFRIS buttons only make sense on submitted documents (docstatus === 1)
    if (frm.doc.docstatus !== 1) {
        console.log("[EFRIS JS v2.1] docstatus != 1 — no EFRIS buttons added");
        return;
    }

    // ──────────────────────────────────────────────────────────────────────────
    // PRIMARY ACTION: Upload to EFRIS
    // Show this whenever no URA invoice_id has been assigned yet.
    // ──────────────────────────────────────────────────────────────────────────
    if (!frm.doc.efris_invoice_id) {

        var is_return = (frm.doc.is_return === 1 || frm.doc.is_return === true);
        var btn_label = is_return
            ? __("Upload Credit Note to EFRIS")
            : __("Upload to EFRIS");

        console.log("[EFRIS JS v2.1] adding standalone primary button: " + btn_label);

        // Third argument intentionally omitted → standalone button (not in dropdown)
        var $primary = frm.add_custom_button(btn_label, function () {
            frappe.confirm(
                __("Submit this invoice to URA EFRIS?"),
                function () {                         // on YES
                    if (is_return) {
                        _credit_note_dialog(frm);
                    } else {
                        frappe.call({
                            method: "efris_integration.api.invoice.submit_sales_invoice",
                            args: { invoice_name: frm.doc.name },
                            freeze: true,
                            freeze_message: __("Submitting to EFRIS — please wait..."),
                            callback: function (r) {
                                if (!r.exc) {
                                    frm.reload_doc();
                                }
                            }
                        });
                    }
                }
            );
        });

        // Paint it blue so it stands out in the toolbar
        if ($primary && typeof $primary.addClass === "function") {
            $primary.addClass("btn-primary");
        }

    }

    // ──────────────────────────────────────────────────────────────────────────
    // SECONDARY ACTIONS — all inside the "EFRIS" dropdown group
    // ──────────────────────────────────────────────────────────────────────────

    // "Refresh from EFRIS" — show when:
    //   (a) invoice already has a URA invoice_id  (normal refresh / re-query)
    //   (b) efris_status is "Pending"             (T108 returned no details — need T106)
    var needs_refresh = frm.doc.efris_invoice_id
        || frm.doc.efris_status === "Pending"
        || frm.doc.efris_status === "Submitted";

    if (needs_refresh) {
        frm.add_custom_button(__("Refresh from EFRIS"), function () {
            frappe.call({
                method: "efris_integration.api.invoice.fetch_invoice_from_ura",
                args: { invoice_name: frm.doc.name },
                freeze: true,
                freeze_message: __("Fetching invoice details from URA..."),
                callback: function (r) {
                    if (!r.exc) {
                        if (r.message && r.message.status === "not_found") {
                            frappe.show_alert({
                                message: r.message.message
                                    || __("Invoice not yet visible on URA. Try again shortly."),
                                indicator: "orange"
                            });
                        } else {
                            frappe.show_alert({
                                message: __("URA details fetched successfully"),
                                indicator: "green"
                            });
                        }
                        frm.reload_doc();
                    }
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
                frappe.msgprint(__("QR viewer not loaded. Run: bench build --app efris_integration"));
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

    console.log("[EFRIS JS v2.1] buttons rendered OK");
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
                    reason_code:  values.reason_code,
                    reason:       values.reason || ""
                },
                freeze: true,
                freeze_message: __("Submitting Credit Note to EFRIS..."),
                callback: function (r) {
                    if (!r.exc) {
                        frappe.show_alert({
                            message: __("Credit Note submitted to EFRIS"),
                            indicator: "green"
                        });
                        frm.reload_doc();
                    }
                }
            });
        }
    });
    d.show();
}
