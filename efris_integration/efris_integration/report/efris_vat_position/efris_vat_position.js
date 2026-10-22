/**
 * EFRIS VAT Position: output VAT vs input VAT, ERPNext beside URA.
 * Open the sales / purchase reconciliations to see the invoices behind a difference.
 * Mirrors: VatPositionSummary.cs
 */

frappe.query_reports["EFRIS VAT Position"] = {

    filters: [
        {
            fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company",
            default: frappe.defaults.get_user_default("Company"), reqd: 1,
        },
        { fieldname: "from_date", label: __("From Date"), fieldtype: "Date", reqd: 1, default: frappe.datetime.month_start() },
        { fieldname: "to_date", label: __("To Date"), fieldtype: "Date", reqd: 1, default: frappe.datetime.get_today() },
        { fieldname: "tolerance", label: __("Amount Tolerance"), fieldtype: "Float", default: 100 },
    ],

    onload(report) {
        const open = (name) => frappe.set_route("query-report", name, {
            company: frappe.query_report.get_filter_value("company"),
            from_date: frappe.query_report.get_filter_value("from_date"),
            to_date: frappe.query_report.get_filter_value("to_date"),
        });
        report.page.add_inner_button(__("Sales Reconciliation"), () => open("EFRIS Sales Reconciliation"));
        report.page.add_inner_button(__("Purchase Reconciliation"), () => open("EFRIS Purchase Reconciliation"));
        report.page.add_inner_button(__("Refresh from URA"), () => {
            frappe.call({
                method: "efris_integration.api.ura_data.clear_cache",
                args: { company: frappe.query_report.get_filter_value("company") },
                callback: () => report.refresh(),
            });
        });
    },
};
