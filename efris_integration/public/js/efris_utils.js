/**
 * efris_utils.js
 * ──────────────────────────────────────────────────────────────────────────
 * Shared helpers loaded on ALL pages via app_include_js in hooks.py.
 * Every doctype-specific JS file uses these functions.
 *
 * Mirrors: clsUIGeneric.cs (error log, message display), CommonValues.cs
 */

// ─── Status badge ──────────────────────────────────────────────────────────
/**
 * Shows a small EFRIS status indicator in the form's dashboard area.
 * Uses frm.dashboard.add_indicator() which is the correct Frappe v15/v16 API.
 * Wrapped in try-catch so a missing API never breaks button rendering.
 */
window.efris_add_status_badge = function (frm) {
    if (!frm.doc.efris_status) return;

    const colour_map = {
        "Submitted":              "green",
        "Credit Note Submitted":  "green",
        "Pending":                "yellow",
        "Failed":                 "red",
    };
    const colour = colour_map[frm.doc.efris_status] || "gray";

    try {
        // add_indicator is the standard Frappe v14/v15/v16 dashboard API
        if (frm.dashboard && typeof frm.dashboard.add_indicator === "function") {
            frm.dashboard.add_indicator(
                `EFRIS: ${frm.doc.efris_status}`, colour
            );
        }
        if (frm.doc.efris_invoice_id) {
            if (frm.dashboard && typeof frm.dashboard.add_comment === "function") {
                frm.dashboard.add_comment(
                    `EFRIS Invoice ID: <b>${frm.doc.efris_invoice_id}</b>`,
                    "blue", true
                );
            }
        }
    } catch (e) {
        // Never let a dashboard API change break the rest of the form
        console.warn("EFRIS: status badge render failed —", e);
    }
};

// ─── Generic EFRIS API call ────────────────────────────────────────────────
/**
 * Call an EFRIS whitelisted method, reload doc on success.
 */
window.efris_call = function (frm, method, args, reload = true) {
    frappe.call({
        method,
        args,
        freeze: true,
        freeze_message: __("Submitting to EFRIS…"),
        callback(r) {
            if (!r.exc) {
                frappe.show_alert({
                    message: __("EFRIS submission successful"),
                    indicator: "green"
                });
                if (reload) frm.reload_doc();
            }
        },
        error(r) {
            frappe.msgprint({
                title: __("EFRIS Submission Failed"),
                message: r.message || __("An unknown error occurred."),
                indicator: "red",
            });
        }
    });
};

// ─── Confirm-then-call wrapper ─────────────────────────────────────────────
window.efris_confirm_and_call = function (frm, label, action_fn) {
    frappe.confirm(
        __(`Are you sure you want to: <b>${label}</b>?`),
        action_fn
    );
};

// ─── Open EFRIS Log list filtered to this doc ──────────────────────────────
window.efris_open_log = function (frm) {
    frappe.set_route("List", "EFRIS Log", {
        document_type: frm.doctype,
        document_name: frm.doc.name,
    });
};

// ─── QR code displayer ────────────────────────────────────────────────────
/**
 * If the form has an efris_qr_code field, render it in a dialog popup.
 */
window.efris_show_qr = function (frm) {
    if (!frm.doc.efris_qr_code) {
        frappe.msgprint(__("No QR code available yet."));
        return;
    }
    const d = new frappe.ui.Dialog({
        title: __("EFRIS QR Code – ") + frm.doc.name,
        fields: [{
            fieldtype: "HTML",
            fieldname: "qr_html",
        }],
    });
    d.fields_dict.qr_html.$wrapper.html(
        `<div style="text-align:center;padding:20px;">
            <img src="${frm.doc.efris_qr_code}" style="max-width:280px;" />
            <p style="font-size:11px;color:#888;">Scan with URA EFRIS app</p>
         </div>`
    );
    d.show();
};
