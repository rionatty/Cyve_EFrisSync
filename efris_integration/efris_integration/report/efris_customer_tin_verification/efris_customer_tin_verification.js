/**
 * EFRIS Customer TIN Verification: tick customers, then
 *   "Verify Selected"             saves Business Type and Name on URA
 *   "Verify & Update Names"       also renames the customer to the URA legal name
 * Customers without a TIN are skipped. Mirrors: BulkBPVerify.cs
 */

frappe.query_reports["EFRIS Customer TIN Verification"] = {

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
            fieldname: "show",
            label: __("Show"),
            fieldtype: "Select",
            options: "Not Verified\nAll",
            default: "Not Verified",
        },
        { fieldname: "customer_group", label: __("Customer Group"), fieldtype: "Link", options: "Customer Group" },
        { fieldname: "with_tin_only", label: __("Only customers with a TIN"), fieldtype: "Check", default: 1 },
    ],

    get_datatable_options(options) {
        return Object.assign(options, { checkboxColumn: true });
    },

    onload(report) {
        const verify = (update_name) => {
            const names = efris_report_checked(report);
            if (!names.length) return;
            frappe.confirm(
                update_name
                    ? __("Verify {0} customer(s) with URA and rename them to the URA legal name?", [names.length])
                    : __("Verify {0} customer(s) with URA?", [names.length]),
                () => efris_report_run(report, "efris_integration.api.bulk.bulk_verify_customers", {
                    names: names,
                    company: frappe.query_report.get_filter_value("company"),
                    update_name: update_name ? 1 : 0,
                }, __("Verifying TINs with URA..."))
            );
        };
        report.page.add_inner_button(__("Verify Selected"), () => verify(false));
        report.page.add_inner_button(__("Verify & Update Names"), () => verify(true));
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
