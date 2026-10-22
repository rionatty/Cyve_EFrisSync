/**
 * EFRIS Invoice Import: URA invoices not in ERPNext yet.
 * Tick rows, then "Create Draft Invoices" creates draft Sales / Purchase Invoices
 * (map URA goods codes in EFRIS Item Mapping first).
 * Mirrors: BulkSalesImport.cs, BulkPurchaseImport.cs
 */

frappe.query_reports["EFRIS Invoice Import"] = {

    filters: [
        {
            fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company",
            default: frappe.defaults.get_user_default("Company"), reqd: 1,
        },
        { fieldname: "direction", label: __("Invoices"), fieldtype: "Select", options: "Sales\nPurchases", default: "Purchases" },
        {
            fieldname: "from_date", label: __("From Date"), fieldtype: "Date", reqd: 1,
            default: frappe.datetime.add_days(frappe.datetime.get_today(), -7),
        },
        { fieldname: "to_date", label: __("To Date"), fieldtype: "Date", reqd: 1, default: frappe.datetime.get_today() },
    ],

    get_datatable_options(options) {
        return Object.assign(options, { checkboxColumn: true });
    },

    onload(report) {
        report.page.add_inner_button(__("Create Draft Invoices"), () => {
            const dt = report.datatable;
            const fdns = (dt ? dt.rowmanager.getCheckedRows() : [])
                .map((i) => report.data[i] && report.data[i].name).filter(Boolean);
            if (!fdns.length) {
                frappe.msgprint(__("Tick at least one invoice first."));
                return;
            }
            const purchases = frappe.query_report.get_filter_value("direction") === "Purchases";
            frappe.confirm(
                __("Create {0} draft {1} from URA? Credit notes become returns against their original invoice.",
                    [fdns.length, purchases ? __("Purchase Invoices") : __("Sales Invoices")]),
                () => frappe.call({
                    method: "efris_integration.api.ura_import.create_drafts",
                    args: {
                        company: frappe.query_report.get_filter_value("company"),
                        direction: purchases ? "Purchase" : "Sales",
                        fdns: fdns,
                    },
                    freeze: true,
                    freeze_message: __("Reading invoices from URA..."),
                    callback(r) {
                        const res = r.message || {};
                        const esc = frappe.utils.escape_html;
                        let html = __("{0} draft(s) created, {1} failed, {2} skipped.",
                            [(res.done || []).length, (res.failed || []).length, (res.skipped || []).length]);
                        (res.failed || []).forEach((f) => { html += "<br><b>" + esc(f.name) + "</b>: " + esc(f.error); });
                        (res.skipped || []).forEach((s) => { html += "<br><b>" + esc(s.name) + "</b>: " + esc(s.reason); });
                        frappe.msgprint({ title: __("EFRIS Import"), message: html,
                            indicator: (res.failed || []).length ? "orange" : "green" });
                        report.refresh();
                    },
                })
            );
        });

        report.page.add_inner_button(__("Item Mapping"), () => frappe.set_route("List", "EFRIS Item Mapping"));

        report.page.add_inner_button(__("Refresh from URA"), () => {
            frappe.call({
                method: "efris_integration.api.ura_data.clear_cache",
                args: { company: frappe.query_report.get_filter_value("company") },
                callback: () => report.refresh(),
            });
        });
    },
};
