/**
 * efris_utils.js
 * Shared EFRIS helpers — loaded on every Desk page via app_include_js.
 * CRITICAL: this file must NEVER throw at parse time or at call time.
 *   - No bare statements at module level (only function assignments)
 *   - All runtime calls wrapped in try-catch
 *   - No emoji or non-ASCII characters (encoding safety)
 *   - No optional chaining / nullish coalescing (older browser compat)
 */

/* global frappe, __ */

// ── Status badge ──────────────────────────────────────────────────────────────
window.efris_add_status_badge = function (frm) {
    try {
        if (!frm || !frm.doc || !frm.doc.efris_status) return;

        var colour_map = {
            "Submitted":             "green",
            "Credit Note Submitted": "green",
            "Pending":               "yellow",
            "Failed":                "red"
        };
        var colour = colour_map[frm.doc.efris_status] || "gray";

        // add_indicator exists in Frappe v13-v16; guard before calling
        if (frm.dashboard && typeof frm.dashboard.add_indicator === "function") {
            frm.dashboard.add_indicator("EFRIS: " + frm.doc.efris_status, colour);
        }
    } catch (e) {
        // Never propagate — badge is cosmetic, must not break form buttons
        console.warn("EFRIS status badge error:", e);
    }
};


// ── Generic EFRIS API call ─────────────────────────────────────────────────────
window.efris_call = function (frm, method, args, reload) {
    if (reload === undefined) reload = true;
    frappe.call({
        method: method,
        args: args,
        freeze: true,
        freeze_message: __("Communicating with EFRIS..."),
        callback: function (r) {
            if (!r.exc) {
                frappe.show_alert({ message: __("EFRIS: done"), indicator: "green" });
                if (reload) frm.reload_doc();
            }
        },
        error: function (r) {
            frappe.msgprint({
                title: __("EFRIS Error"),
                message: (r && r.message) ? r.message : __("An unknown error occurred."),
                indicator: "red"
            });
        }
    });
};


// ── Ask before sending to EFRIS ───────────────────────────────────────────────
// efris_confirm_and_call(frm, message, fn): show `message`, run `fn` on Yes.
window.efris_confirm_and_call = function (frm, message, fn) {
    frappe.confirm(message, function () {
        try {
            fn();
        } catch (e) {
            console.warn("EFRIS action error:", e);
        }
    });
};


// ── Open EFRIS Log list filtered to this document ─────────────────────────────
window.efris_open_log = function (frm) {
    frappe.set_route("List", "EFRIS Log", {
        document_type: frm.doctype,
        document_name: frm.doc.name
    });
};


// ── QR code viewer ────────────────────────────────────────────────────────────
/**
 * Show the EFRIS QR code in a dialog popup.
 *
 * efris_qr_code  = base64 PNG data-URI  (stored at submission time)
 * efris_qr_path  = URA validation URL   (fallback for older records)
 *
 * Flow:
 *   1. If efris_qr_code is a data:image/ URI  -> render <img> immediately
 *   2. Else call server to generate QR from efris_qr_path (or efris_qr_code)
 */
window.efris_show_qr = function (frm) {
    var b64  = (frm.doc.efris_qr_code  || "").trim();
    var url  = (frm.doc.efris_qr_path  || "").trim();
    var fdn  = frm.doc.efris_fdn_number || frm.doc.name;

    function _render(img_src) {
        var d = new frappe.ui.Dialog({
            title: __("EFRIS QR Code") + " - " + fdn,
            fields: [{ fieldtype: "HTML", fieldname: "qr_html" }]
        });

        var link_part = url
            ? '<p style="margin-top:10px;">'
              + '<a href="' + url + '" target="_blank" '
              + 'style="font-size:12px;color:#1a73e8;">'
              + '[Verify on URA EFRIS]</a></p>'
            : "";

        d.fields_dict.qr_html.$wrapper.html(
            '<div style="text-align:center;padding:20px;">'
            + '<img src="' + img_src + '" '
            + 'style="max-width:260px;border:1px solid #eee;padding:8px;" />'
            + '<p style="font-size:11px;color:#888;margin-top:8px;">'
            + 'Scan with URA EFRIS app to verify invoice'
            + '</p>'
            + link_part
            + '</div>'
        );
        d.show();
    }

    // Case 1 — already have a base64 PNG
    if (b64 && b64.indexOf("data:image/") === 0) {
        _render(b64);
        return;
    }

    // Case 2 — generate from URL server-side
    var target = url || b64;
    if (!target) {
        frappe.msgprint(__("No QR code available for this invoice yet."));
        return;
    }

    frappe.call({
        method: "efris_integration.api.invoice.get_qr_code_image",
        args: { validation_url: target },
        freeze: true,
        freeze_message: __("Generating QR code..."),
        callback: function (r) {
            if (r && r.message) {
                _render(r.message);
            } else {
                frappe.msgprint(__("Could not generate QR code."));
            }
        }
    });
};
