/**
 * EFRIS Sales Reconciliation: ERPNext sales vs URA, matched by FDN, reference,
 * then party + amount within the tolerance. Mirrors: SalesReconciliation.cs
 */

frappe.query_reports["EFRIS Sales Reconciliation"] = {

    filters: [
        {
            fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company",
            default: frappe.defaults.get_user_default("Company"), reqd: 1,
        },
        {
            fieldname: "from_date", label: __("From Date"), fieldtype: "Date", reqd: 1,
            default: frappe.datetime.month_start(),
        },
        { fieldname: "to_date", label: __("To Date"), fieldtype: "Date", reqd: 1, default: frappe.datetime.get_today() },
        {
            fieldname: "status", label: __("Status"), fieldtype: "Select",
            options: "All\nAmount Difference\nURA Only\nERPNext Only\nMatched", default: "All",
        },
        { fieldname: "tolerance", label: __("Amount Tolerance"), fieldtype: "Float", default: 100 },
    ],

    formatter(value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        if (column.fieldname === "status" && data) {
            const colour = { "Matched": "green", "Amount Difference": "red" }[data.status] || "orange";
            return '<span class="indicator-pill ' + colour + '">' + value + "</span>";
        }
        return value;
    },

    onload(report) {
        report.page.add_inner_button(__("Refresh from URA"), () => {
            frappe.call({
                method: "efris_integration.api.ura_data.clear_cache",
                args: { company: frappe.query_report.get_filter_value("company") },
                callback: () => report.refresh(),
            });
        });
    },
};
