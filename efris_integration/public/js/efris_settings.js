/**
 * EFRIS Settings — mirrors URASetting.cs exactly.
 *
 * SAP B1 buttons (from etItemPressAfterAction):
 *   BtnTest  → TestEFRISConnection()  → T101
 *   BtnFetch → FetchAndUpdateExchangeRates() → T126
 *
 * SAP B1 mutual exclusion: Alcohol Industry XOR Beverage Industry
 * SAP B1 tabs: Connection | Settings  (Folder items FoldrConn / FoldrSett)
 */

frappe.ui.form.on("EFRIS Settings", {

    // ── On form load ──────────────────────────────────────────────────────
    refresh(frm) {
        _add_action_buttons(frm);
        _add_init_buttons(frm);
        _show_init_status(frm);
    },

    // ── Mutual exclusion: Alcohol XOR Beverage (mirrors SAP B1 UDF validation) ──
    alcohol_industry(frm) {
        if (frm.doc.alcohol_industry && frm.doc.beverage_industry) {
            frm.set_value("beverage_industry", 0);
            frappe.show_alert({
                message: __("Alcohol Industry and Beverage Industry are mutually exclusive. Beverage Industry has been unchecked."),
                indicator: "orange",
            });
        }
    },
    beverage_industry(frm) {
        if (frm.doc.beverage_industry && frm.doc.alcohol_industry) {
            frm.set_value("alcohol_industry", 0);
            frappe.show_alert({
                message: __("Alcohol Industry and Beverage Industry are mutually exclusive. Alcohol Industry has been unchecked."),
                indicator: "orange",
            });
        }
    },
});


// ─── Primary action buttons (always visible, mirror SAP BtnTest / BtnFetch) ──

function _add_action_buttons(frm) {

    // ── Test Connection (SAP: BtnTest → TestEFRISConnection → T101) ──────
    frm.add_custom_button(__("Test Connection"), () => {
        frappe.call({
            method: "efris_integration.api.auth.get_server_time",
            freeze: true,
            freeze_message: __("Testing EFRIS connection (T101)…"),
            callback(r) {
                if (!r.exc && r.message) {
                    frappe.msgprint({
                        title:   __("Connection Successful"),
                        message: `<p>✅ EFRIS endpoint reachable</p>
                                  <p><b>Server time:</b> ${r.message}</p>
                                  <p><b>URL:</b> ${frm.doc.api_url}</p>`,
                        indicator: "green",
                    });
                }
            }
        });
    });

    // ── Fetch URA Exchange Rates (SAP: BtnFetch → FetchAndUpdateExchangeRates → T126) ──
    frm.add_custom_button(__("Fetch URA Exchange Rates"), () => {
        frappe.call({
            method: "efris_integration.api.exchange_rate.sync_exchange_rates",
            freeze: true,
            freeze_message: __("Fetching exchange rates from URA (T126)…"),
            callback(r) {
                if (!r.exc && r.message) {
                    frappe.msgprint({
                        title:   __("Exchange Rates Updated"),
                        message: `✅ ${r.message.synced} currencies updated for ${r.message.date}.`,
                        indicator: "green",
                    });
                }
            }
        });
    });
}


// ─── Init-sequence buttons (Connection tab, for troubleshooting) ─────────────

function _add_init_buttons(frm) {

    frm.add_custom_button(__("🔑 Full Initialise (T101→T104)"), () => {
        frappe.confirm(
            __("Run the full EFRIS initialisation sequence?<br>"
             + "<b>T101</b> Test → <b>T102</b> Keys → <b>T103</b> Sign In → <b>T104</b> Session Key"),
            () => {
                frappe.call({
                    method: "efris_integration.api.auth.full_initialise",
                    freeze: true,
                    freeze_message: __("Running EFRIS initialisation…"),
                    callback(r) {
                        if (!r.exc) frm.reload_doc();
                    }
                });
            }
        );
    }, __("Initialise"));

    frm.add_custom_button(__("T102 – Get Keys"), () => {
        _run_init_step(frm, "efris_integration.api.auth.client_init",
                       __("Getting client keys (T102)…"));
    }, __("Initialise"));

    frm.add_custom_button(__("T103 – Sign In"), () => {
        _run_init_step(frm, "efris_integration.api.auth.sign_in",
                       __("Signing in to EFRIS (T103)…"));
    }, __("Initialise"));

    frm.add_custom_button(__("T104 – Session Key"), () => {
        _run_init_step(frm, "efris_integration.api.auth.get_symmetric_key",
                       __("Retrieving session AES key (T104)…"));
    }, __("Initialise"));
}


// ─── Status indicator on dashboard (mirrors SAP B1 status bar messages) ──────

function _show_init_status(frm) {
    const status = frm.doc.init_status;
    if (!status) return;

    const ok = status.includes("OK") || status.includes("active");
    frm.dashboard.add_comment(
        `<span class="indicator-pill ${ok ? "green" : "orange"}">EFRIS: ${status}</span>`,
        ok ? "green" : "orange", true
    );

    if (frm.doc.token_expiry) {
        const diff = Math.round(
            (new Date(frm.doc.token_expiry) - new Date()) / 60000
        );
        const colour = diff < 30 ? "red" : diff < 120 ? "orange" : "green";
        frm.dashboard.add_comment(
            `<span class="indicator-pill ${colour}">Session expires in ${diff} min</span>`,
            colour, true
        );
    }
}


// ─── Generic init-step helper ─────────────────────────────────────────────────

function _run_init_step(frm, method, freeze_msg) {
    frappe.call({
        method,
        freeze: true,
        freeze_message: freeze_msg,
        callback(r) {
            if (!r.exc) {
                frappe.show_alert({ message: __("Done ✅"), indicator: "green" });
                frm.reload_doc();
            }
        }
    });
}
