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

    // Credit notes have their own lifecycle (upload, approval, cancel)
    var is_return = (frm.doc.is_return === 1 || frm.doc.is_return === true);
    if (is_return) {
        _credit_note_buttons(frm);
        return;
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

        // Third argument intentionally omitted → standalone button (not in dropdown)
        // Checks URA stock first (T128); a shortage only warns, as in SAP B1.
        var $primary = frm.add_custom_button(__("Upload to EFRIS"), function () {
            var upload = function () {
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
            };
            _efris_stock_check(frm, function (res) {
                if (res && (res.shortage || res.problems)) {
                    _efris_stock_dialog(res, __("Upload Anyway"), upload);
                } else {
                    frappe.confirm(__("Submit this invoice to URA EFRIS?"), upload);
                }
            });
        });

        // Paint it blue so it stands out in the toolbar
        if ($primary && typeof $primary.addClass === "function") {
            $primary.addClass("btn-primary");
        }

    }

    // ──────────────────────────────────────────────────────────────────────────
    // SECONDARY ACTIONS — all inside the "EFRIS" dropdown group
    // ──────────────────────────────────────────────────────────────────────────

    // "Update EFRIS Details" — finds this invoice on URA by its reference number
    // (T106) and refills FDN / Invoice ID / verification code / QR (T108).
    // Highlighted when URA confirmed the upload but details are missing.
    var details_missing = !frm.doc.efris_fdn_number || !frm.doc.efris_antifake_code
        || !frm.doc.efris_invoice_id || !frm.doc.efris_qr_path;
    var update_details = function () {
        frappe.call({
            method: "efris_integration.api.invoice.fetch_invoice_from_ura",
            args: { invoice_name: frm.doc.name },
            freeze: true,
            freeze_message: __("Looking up this invoice on URA..."),
            callback: function (r) {
                if (!r.exc) {
                    if (r.message && r.message.status === "not_found") {
                        frappe.msgprint({ message: r.message.message, indicator: "orange", title: __("EFRIS") });
                    } else {
                        frappe.show_alert({ message: __("EFRIS details updated"), indicator: "green" });
                    }
                    frm.reload_doc();
                }
            }
        });
    };
    if (frm.doc.efris_status && details_missing) {
        frm.add_custom_button(__("Update EFRIS Details"), update_details);
    } else {
        frm.add_custom_button(__("Update EFRIS Details"), update_details, __("EFRIS"));
    }

    frm.add_custom_button(__("Check EFRIS Stock"), function () {
        _efris_stock_check(frm, function (res) {
            if (res) _efris_stock_dialog(res);
        });
    }, __("EFRIS"));

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


// ── URA stock check (T128) ────────────────────────────────────────────────────
// Calls back with the result, or with null if the check itself failed
// (then the upload simply goes ahead after a normal confirmation).

function _efris_stock_check(frm, done) {
    frappe.call({
        method: "efris_integration.api.invoice.check_ura_stock",
        args: { invoice_name: frm.doc.name },
        freeze: true,
        freeze_message: __("Checking stock on URA..."),
        callback: function (r) { done(r.message || null); },
        error: function () { done(null); }
    });
}

function _efris_stock_dialog(res, action_label, action) {
    var esc = frappe.utils.escape_html;
    var rows = (res.rows || []).map(function (r) {
        var colour = r.shortage > 0 ? "#c0392b" : (r.note ? "#b7791f" : "inherit");
        return "<tr style='color:" + colour + "'>"
            + "<td>" + esc(r.item_code) + (r.branch_id ? " <small>(" + esc(r.branch_id) + ")</small>" : "") + "</td>"
            + "<td style='text-align:right'>" + format_number(r.needed) + "</td>"
            + "<td style='text-align:right'>" + (r.ura_stock === null ? "-" : format_number(r.ura_stock)) + "</td>"
            + "<td style='text-align:right'>" + (r.shortage > 0 ? format_number(r.shortage) : "") + "</td>"
            + "<td>" + esc(r.note || "") + "</td></tr>";
    }).join("");
    var html = (res.rows || []).length
        ? "<table class='table table-bordered table-sm'><thead><tr><th>" + __("Item") + "</th><th>"
            + __("Invoice Qty") + "</th><th>" + __("URA Stock") + "</th><th>" + __("Short") + "</th><th>"
            + __("Note") + "</th></tr></thead><tbody>" + rows + "</tbody></table>"
        : __("No stock items on this invoice.");
    var title = res.shortage ? __("URA Stock Shortage") : __("URA Stock");
    var d = new frappe.ui.Dialog({ title: title, fields: [{ fieldtype: "HTML", fieldname: "t" }] });
    d.fields_dict.t.$wrapper.html(html);
    if (action) {
        d.set_primary_action(action_label, function () { d.hide(); action(); });
    }
    d.show();
}


// ── Credit note buttons ───────────────────────────────────────────────────────
//   not sent / rejected / voided -> [Upload Credit Note to EFRIS]  (primary)
//   Pending                      -> [Check EFRIS Status]           (primary)
//   Pending or Approved          -> EFRIS > Cancel in EFRIS (also after ERPNext cancel)

function _credit_note_buttons(frm) {
    var ref = frm.doc.efris_cn_reference_no;
    var approval = frm.doc.efris_cn_approval || "";
    var can_resend = !ref || approval === "Rejected" || approval === "Voided";

    function primary(label, fn) {
        var $b = frm.add_custom_button(label, fn);
        if ($b && typeof $b.addClass === "function") $b.addClass("btn-primary");
    }

    if (frm.doc.docstatus === 1 && can_resend) {
        primary(__("Upload Credit Note to EFRIS"), function () { _credit_note_dialog(frm); });
    }

    if (ref && frm.doc.docstatus >= 1) {
        var check = function () {
            frappe.call({
                method: "efris_integration.api.credit_note.check_credit_note_status",
                args: { invoice_name: frm.doc.name },
                freeze: true,
                freeze_message: __("Checking credit note status with URA..."),
                callback: function (r) { if (!r.exc) frm.reload_doc(); }
            });
        };
        if (approval === "Pending") {
            primary(__("Check EFRIS Status"), check);
        } else {
            frm.add_custom_button(__("Check EFRIS Status"), check, __("EFRIS"));
        }
    }

    if (ref && (approval === "Pending" || approval === "Approved") && frm.doc.docstatus >= 1) {
        frm.add_custom_button(__("Cancel in EFRIS"), function () {
            efris_confirm_and_call(frm,
                approval === "Pending"
                    ? __("Void credit note application {0} in EFRIS?", [ref])
                    : __("Ask URA to cancel approved credit note {0}?", [frm.doc.efris_fdn_number || ref]),
                function () {
                    efris_call(frm, "efris_integration.api.credit_note.cancel_credit_note_in_efris",
                        { invoice_name: frm.doc.name });
                });
        }, __("EFRIS"));
    }

    if (frm.doc.efris_qr_path) {
        frm.add_custom_button(__("View QR Code"), function () { efris_show_qr(frm); }, __("EFRIS"));
        frm.add_custom_button(__("Verify on URA"), function () {
            window.open(frm.doc.efris_qr_path, "_blank");
        }, __("EFRIS"));
    }

    if (frm.doc.docstatus >= 1) {
        frm.add_custom_button(__("EFRIS Log"), function () { efris_open_log(frm); }, __("EFRIS"));
    }
}


// ── Credit Note reason dialog ─────────────────────────────────────────────────

function _credit_note_dialog(frm) {
    var d = new frappe.ui.Dialog({
        title: __("Upload Credit Note to EFRIS"),
        fields: [
            {
                label: __("Reason"),
                fieldname: "reason_code",
                fieldtype: "Select",
                options: [
                    { value: "101", label: __("Return of products due to expiry or damage, etc.") },
                    { value: "102", label: __("Cancellation of the purchase") },
                    { value: "103", label: __("Invoice amount wrong") },
                    { value: "104", label: __("Partial or complete waive off") },
                    { value: "105", label: __("Others (describe below)") }
                ],
                reqd: 1,
                default: "102"
            },
            {
                label: __("Description"),
                fieldname: "reason",
                fieldtype: "Small Text",
                mandatory_depends_on: "eval:doc.reason_code=='105'"
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
