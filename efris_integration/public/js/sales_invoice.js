/**
 * EFRIS Sales Invoice form enhancements.
 *
 * Mirrors SAP B1: AR Invoice -> EFRIS tab
 *   - "Upload to EFRIS" button (T108 for normal, T110 for credit note)
 *   - QR Code, QR Validation URL, Verification Code, FDN Number, Invoice ID, Remark
 *
 * SAFETY RULES (do NOT remove):
 *   1. The entire refresh() body is wrapped in try-catch.
 *      If anything here throws, ERPNext's own refresh() must still run so that
 *      standard buttons (Make Payment, Return, Cancel, Amend) are not lost.
 *   2. frm.add_custom_button() return value is stored before .addClass() is called.
 *      In some Frappe v16 states add_custom_button() returns undefined; chaining
 *      .addClass() directly on that throws TypeError and aborts the handler.
 */

/* global frappe, __, efris_add_status_badge, efris_call, efris_open_log, efris_show_qr */

frappe.ui.form.on("Sales Invoice", {

    refresh: function (frm) {
        // --- SAFETY WRAPPER: never let EFRIS code break standard ERPNext buttons ---
        try {
            _efris_refresh(frm);
        } catch (e) {
            console.error("EFRIS Sales Invoice refresh error:", e);
        }
    }

});


function _efris_refresh(frm) {
    efris_add_status_badge(frm);

    // Custom buttons only make sense on submitted (docstatus=1) documents
    if (frm.doc.docstatus !== 1) return;

    // ── Upload / Refresh ────────────────────────────────────────────────────
    if (!frm.doc.efris_invoice_id) {
        var is_return = frm.doc.is_return;
        var label = is_return
            ? __("Upload Credit Note to EFRIS")
            : __("Upload to EFRIS");

        // Guard: store button reference before calling .addClass()
        var $upload_btn = frm.add_custom_button(label, function () {
            frappe.confirm(
                __("Upload {0} to URA EFRIS?", [frm.doc.name]),
                function () {
                    if (is_return) {
                        _credit_note_dialog(frm);
                    } else {
                        efris_call(
                            frm,
                            "efris_integration.api.invoice.submit_sales_invoice",
                            { invoice_name: frm.doc.name }
                        );
                    }
                }
            );
        }, __("EFRIS"));

        // Safely style the button only if add_custom_button returned an element
        if ($upload_btn && typeof $upload_btn.addClass === "function") {
            $upload_btn.addClass("btn-primary");
        }

    } else {
        // Already submitted to EFRIS — offer a refresh query
        frm.add_custom_button(__("Refresh from EFRIS"), function () {
            efris_call(
                frm,
                "efris_integration.api.invoice.query_invoices",
                { company: frm.doc.company, invoice_no: frm.doc.name },
                false
            );
        }, __("EFRIS"));
    }

    // ── Recalculate Excise ──────────────────────────────────────────────────
    frm.add_custom_button(__("Recalculate Excise"), function () {
        frappe.call({
            method: "efris_integration.excise.recalculate_excise",
            args: { doctype: "Sales Invoice", docname: frm.doc.name },
            callback: function (r) { if (r.message) frm.reload_doc(); }
        });
    }, __("EFRIS"));

    // ── View QR Code ────────────────────────────────────────────────────────
    if (frm.doc.efris_qr_code || frm.doc.efris_qr_path) {
        frm.add_custom_button(__("View QR Code"), function () {
            efris_show_qr(frm);
        }, __("EFRIS"));
    }

    // ── Verify on URA (opens browser tab) ──────────────────────────────────
    if (frm.doc.efris_qr_path) {
        frm.add_custom_button(__("Verify on URA"), function () {
            window.open(frm.doc.efris_qr_path, "_blank");
        }, __("EFRIS"));
    }

    // ── EFRIS Log ───────────────────────────────────────────────────────────
    frm.add_custom_button(__("EFRIS Log"), function () {
        efris_open_log(frm);
    }, __("EFRIS"));
}


// ── Credit Note reason-code dialog ───────────────────────────────────────────

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
            efris_call(
                frm,
                "efris_integration.api.credit_note.submit_credit_note",
                {
                    invoice_name: frm.doc.name,
                    reason_code: values.reason_code,
                    reason: values.reason || ""
                }
            );
        }
    });
    d.show();
}
