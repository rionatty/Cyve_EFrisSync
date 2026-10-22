/**
 * EFRIS Stock Reconciliation: ERPNext vs URA stock for a warehouse.
 * Tick rows, then "Adjust EFRIS Stock" sends stock in / out so URA matches ERPNext.
 * Mirrors: StockReconciliation.cs
 */

frappe.query_reports["EFRIS Stock Reconciliation"] = {

    filters: [
        {
            fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company",
            default: frappe.defaults.get_user_default("Company"), reqd: 1,
        },
        {
            fieldname: "warehouse", label: __("Warehouse"), fieldtype: "Link", options: "Warehouse", reqd: 1,
            get_query: () => ({ filters: { company: frappe.query_report.get_filter_value("company"), is_group: 0 } }),
        },
        { fieldname: "item_group", label: __("Item Group"), fieldtype: "Link", options: "Item Group" },
        { fieldname: "show", label: __("Show"), fieldtype: "Select", options: "Differences only\nAll", default: "Differences only" },
    ],

    get_datatable_options(options) {
        return Object.assign(options, { checkboxColumn: true });
    },

    formatter(value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        if (column.fieldname === "status" && data) {
            const colour = { "Matched": "green", "Different": "red" }[data.status] || "orange";
            return `<span class="indicator-pill ${colour}">${value}</span>`;
        }
        return value;
    },

    onload(report) {
        report.page.add_inner_button(__("Adjust EFRIS Stock"), () => {
            const dt = report.datatable;
            const rows = (dt ? dt.rowmanager.getCheckedRows() : []).map((i) => report.data[i]).filter(Boolean);
            const items = rows.filter((r) => r.status === "Different").map((r) => r.item_code);
            if (!items.length) {
                frappe.msgprint(__("Tick at least one row with status Different."));
                return;
            }
            frappe.confirm(
                __("Change URA stock for {0} item(s) to match ERPNext? This sends stock in / stock out to URA.",
                    [items.length]),
                () => frappe.call({
                    method: "efris_integration.api.stock.adjust_ura_stock",
                    args: {
                        company: frappe.query_report.get_filter_value("company"),
                        warehouse: frappe.query_report.get_filter_value("warehouse"),
                        item_codes: items,
                    },
                    freeze: true,
                    freeze_message: __("Adjusting stock on URA..."),
                    callback(r) {
                        const res = r.message || {};
                        let html = __("Increased on URA: {0}, reduced: {1}.", [res.increased || 0, res.reduced || 0]);
                        if ((res.skipped || []).length) {
                            html += "<br>" + res.skipped.map(frappe.utils.escape_html).join("<br>");
                        }
                        frappe.msgprint({ title: __("EFRIS Stock"), message: html, indicator: "green" });
                        report.refresh();
                    },
                })
            );
        });

        report.page.add_inner_button(__("Refresh from URA"), () => {
            frappe.call({
                method: "efris_integration.api.ura_data.clear_cache",
                args: { company: frappe.query_report.get_filter_value("company") },
                callback: () => report.refresh(),
            });
        });
    },
};
