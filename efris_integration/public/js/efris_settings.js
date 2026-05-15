/**
 * EFRIS Settings form — matches the SAP B1 EFRIS Configuration UI exactly.
 *
 * SAP B1 layout:
 *   Tab 1: Connection  (URL, key paths, Device No, App Id, User Name, Version)
 *   Tab 2: Settings    (USE T127, Gross/Price flags, industry flags, modules)
 *   Buttons: Test Connection | Fetch URA Exchange Rates | OK | Cancel
 *
 * ERPNext equivalent:
 *   Sections mirror the tabs; action buttons in the form toolbar.
 *
 * Mirrors: URASetting.cs, LicenseActivator.cs
 */

frappe.ui.form.on("EFRIS Settings", {

    // ── On form load ──────────────────────────────────────────────────────
    refresh(frm) {
        _add_init_buttons(frm);
        _add_utility_buttons(frm);
        _show_init_status(frm);
    },

    // ── Mutual exclusion: alcohol vs beverage ─────────────────────────────
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


// ─── Initialisation buttons (mirror SAP B1 Connection tab buttons) ─────────

function _add_init_buttons(frm) {

    // ── Test Connection (SAP B1 button) ────────────────────────────────
    frm.add_custom_button(__("Test Connection"), () => {
        frappe.call({
            method: "efris_integration.api.auth.get_server_time",
            freeze: true,
            freeze_message: __("Testing EFRIS connection (T101)…"),
            callback(r) {
                if (!r.exc && r.message) {
                    frappe.msgprint({
                        title: __("Connection Successful"),
                        message: `
                            <div style="font-size:14px;">
                                <p>✅ EFRIS endpoint reachable</p>
                                <p><b>Server Time:</b> ${r.message}</p>
                                <p><b>URL:</b> ${frm.doc.api_url}</p>
                            </div>`,
                        indicator: "green",
                    });
                }
            }
        });
    }, __("Connection"));

    // ── Full Initialise: T101→T102→T103→T104 ─────────────────────────
    frm.add_custom_button(__("🔑 Full Initialise (T101 → T104)"), () => {
        frappe.confirm(
            __("This will run the full EFRIS initialisation sequence:<br>"
             + "<b>T101</b> Test Connection → "
             + "<b>T102</b> Get Keys → "
             + "<b>T103</b> Sign In → "
             + "<b>T104</b> Get Session Key<br><br>"
             + "Continue?"),
            () => {
                frappe.call({
                    method: "efris_integration.api.auth.full_initialise",
                    freeze: true,
                    freeze_message: __("Running EFRIS initialisation…"),
                    callback(r) {
                        if (!r.exc && r.message) {
                            frm.reload_doc();
                        }
                    }
                });
            }
        );
    }, __("Connection"));

    // ── Step-by-step buttons (for troubleshooting) ────────────────────
    frm.add_custom_button(__("T102 – Get Client Keys"), () => {
        _efris_init_call(frm, "efris_integration.api.auth.client_init",
            __("Fetching client keys from EFRIS (T102)…"));
    }, __("Init Steps"));

    frm.add_custom_button(__("T103 – Sign In"), () => {
        _efris_init_call(frm, "efris_integration.api.auth.sign_in",
            __("Signing in to EFRIS (T103)…"));
    }, __("Init Steps"));

    frm.add_custom_button(__("T104 – Get Session Key"), () => {
        _efris_init_call(frm, "efris_integration.api.auth.get_symmetric_key",
            __("Retrieving session AES key (T104)…"));
    }, __("Init Steps"));
}


// ─── Utility buttons (mirror SAP B1 'Fetch URA Exchange Rates' button) ─────

function _add_utility_buttons(frm) {

    // ── Fetch URA Exchange Rates (T126) ───────────────────────────────
    frm.add_custom_button(__("Fetch URA Exchange Rates"), () => {
        frappe.call({
            method: "efris_integration.api.exchange_rate.sync_exchange_rates",
            freeze: true,
            freeze_message: __("Fetching exchange rates from EFRIS (T126/T120)…"),
            callback(r) {
                if (!r.exc && r.message) {
                    frappe.msgprint({
                        title: __("Exchange Rates Updated"),
                        message: `✅ ${r.message.synced} currencies updated for ${r.message.date}.`,
                        indicator: "green",
                    });
                }
            }
        });
    }, __("Utilities"));

    // ── T127 Industry Config ──────────────────────────────────────────
    frm.add_custom_button(__("T127 – Fetch Industry Config"), () => {
        frappe.call({
            method: "efris_integration.api.t127.get_industry_config",
            args: { force_refresh: true },
            freeze: true,
            freeze_message: __("Fetching industry configuration (T127)…"),
            callback(r) {
                if (!r.exc && r.message) {
                    frappe.msgprint({
                        title: __("T127 Industry Config"),
                        message: `<pre style="font-size:11px;">${JSON.stringify(r.message, null, 2)}</pre>`,
                        indicator: "blue",
                    });
                }
            }
        });
    }, __("Utilities"));

    // ── Auto-map tax accounts ─────────────────────────────────────────
    frm.add_custom_button(__("Auto-Map Tax Accounts"), () => {
        frappe.call({
            method: "efris_integration.api.tax_code_setting.auto_map_tax_accounts",
            freeze: true,
            freeze_message: __("Auto-mapping tax accounts to EFRIS codes…"),
            callback(r) {
                if (r.message !== undefined) {
                    frappe.show_alert({
                        message: __(`${r.message.mapped} tax accounts mapped.`),
                        indicator: "green"
                    });
                }
            }
        });
    }, __("Utilities"));

    // ── Auto-map UOMs ─────────────────────────────────────────────────
    frm.add_custom_button(__("Auto-Map UOMs"), () => {
        frappe.call({
            method: "efris_integration.api.tax_code_setting.auto_map_uoms",
            freeze: true,
            freeze_message: __("Auto-mapping UOMs to EFRIS codes…"),
            callback(r) {
                if (r.message !== undefined) {
                    frappe.show_alert({
                        message: __(`${r.message.mapped} UOMs mapped.`),
                        indicator: "green"
                    });
                }
            }
        });
    }, __("Utilities"));

    // ── Open Bulk Submission report ───────────────────────────────────
    frm.add_custom_button(__("Bulk Submission Portal"), () => {
        frappe.set_route("query-report", "EFRIS Bulk Submission");
    }, __("Utilities"));

    // ── Open EFRIS Log ────────────────────────────────────────────────
    frm.add_custom_button(__("View EFRIS Log"), () => {
        frappe.set_route("query-report", "EFRIS Submission Log");
    }, __("Utilities"));
}


// ─── Status indicator in dashboard ─────────────────────────────────────────

function _show_init_status(frm) {
    const status = frm.doc.init_status;
    if (!status) return;

    const is_ok = status.includes("OK") || status.includes("active");
    frm.dashboard.add_comment(
        `<span class="indicator-pill ${is_ok ? 'green' : 'orange'}">`
        + `EFRIS: ${status}</span>`,
        is_ok ? "green" : "orange",
        true
    );

    if (frm.doc.token_expiry) {
        const expiry = frappe.datetime.str_to_obj(frm.doc.token_expiry);
        const now    = new Date();
        const diffMin = Math.round((expiry - now) / 60000);
        const colour = diffMin < 30 ? "red" : diffMin < 120 ? "orange" : "green";
        frm.dashboard.add_comment(
            `<span class="indicator-pill ${colour}">Session expires in ${diffMin} min</span>`,
            colour, true
        );
    }
}


// ─── Generic init step caller ───────────────────────────────────────────────

function _efris_init_call(frm, method, freeze_msg) {
    frappe.call({
        method,
        freeze: true,
        freeze_message: freeze_msg,
        callback(r) {
            if (!r.exc) {
                frappe.show_alert({ message: __("Done"), indicator: "green" });
                frm.reload_doc();
            }
        }
    });
}
