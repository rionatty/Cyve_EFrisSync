/**
 * EFRIS Settings form — per-company configuration
 *
 * Buttons (match the SAP B1 EFRIS Configuration screen):
 *   - Test Connection        → T101
 *   - Fetch URA Exchange Rates → T126
 *
 * Mutual exclusion: Alcohol Industry XOR Beverage Industry
 */

frappe.ui.form.on("EFRIS Settings", {

    // ── On form load ──────────────────────────────────────────────────────
    refresh(frm) {
        _add_primary_buttons(frm);
    },

    // ── Mutual exclusion: Alcohol XOR Beverage ────────────────────────────
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


// ── Primary buttons — Test Connection & Fetch URA Exchange Rates ──────────────

function _add_primary_buttons(frm) {

    // ── Test Connection (T101) ────────────────────────────────────────────
    frm.add_custom_button(__("Test Connection"), () => {
        if (!frm.doc.company) {
            frappe.msgprint(__("Please save the settings for a Company first."));
            return;
        }
        frappe.call({
            method: "efris_integration.efris_integration.doctype.efris_settings.efris_settings.test_connection",
            args: { company: frm.doc.company },
            freeze: true,
            freeze_message: __("Testing EFRIS connection (T101)…"),
            callback(r) {
                if (r.message) {
                    const res = r.message;
                    if (res.success) {
                        frappe.msgprint({
                            title:     __("Connection Successful ✅"),
                            message:   res.message,
                            indicator: "green",
                        });
                    } else {
                        frappe.msgprint({
                            title:     __("Connection Failed ❌"),
                            message:   res.message,
                            indicator: "red",
                        });
                    }
                }
            }
        });
    });

    // ── Fetch URA Exchange Rates (T126) ───────────────────────────────────
    frm.add_custom_button(__("Fetch URA Exchange Rates"), () => {
        if (!frm.doc.company) {
            frappe.msgprint(__("Please save the settings for a Company first."));
            return;
        }

        const today = frappe.datetime.get_today();
        const d = new frappe.ui.Dialog({
            title: __("Fetch URA Exchange Rates"),
            fields: [
                {
                    label:     __("From Date"),
                    fieldname: "from_date",
                    fieldtype: "Date",
                    default:   today,
                    reqd:      1,
                },
                {
                    label:     __("To Date"),
                    fieldname: "to_date",
                    fieldtype: "Date",
                    default:   today,
                    reqd:      1,
                },
            ],
            primary_action_label: __("Fetch Rates"),
            primary_action(values) {
                d.hide();
                frappe.call({
                    method: "efris_integration.api.exchange_rate.sync_exchange_rates",
                    args: {
                        company:   frm.doc.company,
                        from_date: values.from_date,
                        to_date:   values.to_date,
                    },
                    freeze: true,
                    freeze_message: __("Fetching URA exchange rates (T126)…"),
                    callback(r) {
                        if (r.message) {
                            const m = r.message;
                            const hasFailures = m.failed && m.failed.length;
                            frappe.msgprint({
                                title:     __("Exchange Rates Updated"),
                                message:   `✅ ${m.days || 0} day(s) processed: `
                                         + `${m.inserted || 0} added, ${m.updated || 0} updated.`
                                         + (hasFailures
                                             ? `<br>⚠️ Skipped dates: ${m.failed.join(", ")}`
                                             : ""),
                                indicator: hasFailures ? "orange" : "green",
                            });
                        }
                    }
                });
            }
        });
        d.show();
    });
}
