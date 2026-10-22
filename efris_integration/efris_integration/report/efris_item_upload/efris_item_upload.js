/**
 * EFRIS Item Upload: tick items, then "Upload Selected" registers them on URA
 * (Register New) or sends their changes (Update Registered).
 * Mirrors: BulkItemUpload.cs
 */

frappe.query_reports["EFRIS Item Upload"] = {

    filters: [
        {
            fieldname: "company",
            label: __("Company"),
            fieldtype: "Link",
            options: "Company",
            default: frappe.defaults.get_user_default("Company"),
            reqd: 1,
        },
        {
            fieldname: "mode",
            label: __("Mode"),
            fieldtype: "Select",
            options: "Register New\nUpdate Registered",
            default: "Register New",
        },
        { fieldname: "item_group", label: __("Item Group"), fieldtype: "Link", options: "Item Group" },
        { fieldname: "item_code", label: __("Item Code / Name contains"), fieldtype: "Data" },
    ],

    get_datatable_options(options) {
        return Object.assign(options, { checkboxColumn: true });
    },

    onload(report) {
        report.page.add_inner_button(__("Upload Selected"), () => {
            const names = efris_report_checked(report);
            if (!names.length) return;
            const register = frappe.query_report.get_filter_value("mode") !== "Update Registered";
            frappe.confirm(
                register
                    ? __("Register {0} item(s) on URA EFRIS?", [names.length])
                    : __("Send changes of {0} registered item(s) to URA EFRIS?", [names.length]),
                () => efris_report_run(report, "efris_integration.api.bulk.bulk_upload_items", {
                    names: names,
                    company: frappe.query_report.get_filter_value("company"),
                    mode: register ? "register" : "update",
                }, __("Uploading items to EFRIS..."))
            );
        });
    },
};


function efris_report_checked(report) {
    const dt = report.datatable;
    const rows = dt ? dt.rowmanager.getCheckedRows() : [];
    const names = rows.map((i) => report.data[i] && report.data[i].name).filter(Boolean);
    if (!names.length) frappe.msgprint(__("Tick at least one row first."));
    return names;
}


function efris_report_run(report, method, args, message) {
    frappe.call({
        method: method,
        args: args,
        freeze: true,
        freeze_message: message,
        callback(r) {
            const res = r.message || {};
            const done = (res.done || []).length, failed = res.failed || [], skipped = res.skipped || [];
            let html = __("{0} done, {1} failed, {2} skipped.", [done, failed.length, skipped.length]);
            const esc = frappe.utils.escape_html;
            if (failed.length) {
                html += "<br><br>" + failed.map((f) => "<b>" + esc(f.name) + "</b>: " + esc(f.error)).join("<br>");
            }
            if (skipped.length) {
                html += "<br><br>" + skipped.map((s) => "<b>" + esc(s.name) + "</b>: " + esc(s.reason)).join("<br>");
            }
            frappe.msgprint({ title: __("EFRIS"), message: html, indicator: failed.length ? "orange" : "green" });
            report.refresh();
        },
    });
}
