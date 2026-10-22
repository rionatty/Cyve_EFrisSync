/**
 * EFRIS Credit Notes: tick credit notes, then
 *   "Upload Selected"       -> T110 for each (asks for the reason once)
 *   "Check Status Selected" -> T111 for each (fills FDN, verification code, QR when approved)
 * Mirrors: BulkCNVerify.cs
 */

frappe.query_reports["EFRIS Credit Notes"] = {

    filters: [
        {
            fieldname: "company",
            label: __("Company"),
            fieldtype: "Link",
            options: "Company",
            default: frappe.defaults.get_user_default("Company"),
        },
        {
            fieldname: "show",
            label: __("Show"),
            fieldtype: "Select",
            options: "To Upload\nAwaiting Approval\nAll",
            default: "To Upload",
        },
        {
            fieldname: "from_date",
            label: __("From Date"),
            fieldtype: "Date",
            default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
            reqd: 1,
        },
        {
            fieldname: "to_date",
            label: __("To Date"),
            fieldtype: "Date",
            default: frappe.datetime.get_today(),
            reqd: 1,
        },
    ],

    get_datatable_options(options) {
        return Object.assign(options, { checkboxColumn: true });
    },

    onload(report) {
        report.page.add_inner_button(__("Upload Selected"), () => {
            const names = efris_cn_selected(report);
            if (!names.length) return;
            const d = new frappe.ui.Dialog({
                title: __("Upload {0} credit note(s) to EFRIS", [names.length]),
                fields: [
                    {
                        label: __("Reason"),
                        fieldname: "reason_code",
                        fieldtype: "Select",
                        reqd: 1,
                        default: "102",
                        options: [
                            { value: "101", label: __("Return of products due to expiry or damage, etc.") },
                            { value: "102", label: __("Cancellation of the purchase") },
                            { value: "103", label: __("Invoice amount wrong") },
                            { value: "104", label: __("Partial or complete waive off") },
                            { value: "105", label: __("Others (describe below)") },
                        ],
                    },
                    {
                        label: __("Description"),
                        fieldname: "reason",
                        fieldtype: "Small Text",
                        depends_on: "eval:doc.reason_code=='105'",
                    },
                ],
                primary_action_label: __("Upload"),
                primary_action(values) {
                    d.hide();
                    efris_cn_run(report, "efris_integration.api.credit_note.bulk_submit_credit_notes",
                        { names: names, reason_code: values.reason_code, reason: values.reason || "" },
                        __("Uploading credit notes to EFRIS..."));
                },
            });
            d.show();
        });

        report.page.add_inner_button(__("Check Status Selected"), () => {
            const names = efris_cn_selected(report);
            if (!names.length) return;
            efris_cn_run(report, "efris_integration.api.credit_note.bulk_check_credit_notes",
                { names: names }, __("Checking credit note status with URA..."));
        });
    },
};


function efris_cn_selected(report) {
    const dt = report.datatable;
    const rows = dt ? dt.rowmanager.getCheckedRows() : [];
    const names = rows.map((i) => report.data[i] && report.data[i].name).filter(Boolean);
    if (!names.length) {
        frappe.msgprint(__("Tick at least one credit note first."));
    }
    return names;
}


function efris_cn_run(report, method, args, message) {
    frappe.call({
        method: method,
        args: args,
        freeze: true,
        freeze_message: message,
        callback(r) {
            const res = r.message || { done: [], failed: [] };
            let html = __("{0} done, {1} failed.", [res.done.length, res.failed.length]);
            if (res.failed.length) {
                html += "<br><br>" + res.failed
                    .map((f) => "<b>" + frappe.utils.escape_html(f.name) + "</b>: " + frappe.utils.escape_html(f.error))
                    .join("<br>");
            }
            frappe.msgprint({
                title: __("EFRIS Credit Notes"),
                message: html,
                indicator: res.failed.length ? "orange" : "green",
            });
            report.refresh();
        },
    });
}
