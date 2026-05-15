/**
 * EFRIS Bulk Submission report filters + bulk-submit action button.
 * Mirrors: BulkPosting.cs
 */

frappe.query_reports["EFRIS Bulk Submission"] = {

    filters: [
        {
            fieldname: "doctype",
            label: __("Document Type"),
            fieldtype: "Select",
            options: "Sales Invoice\nPurchase Receipt\nStock Entry",
            default: "Sales Invoice",
            reqd: 1,
            on_change() { frappe.query_report.refresh(); }
        },
        {
            fieldname: "from_date",
            label: __("From Date"),
            fieldtype: "Date",
            default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
        },
        {
            fieldname: "to_date",
            label: __("To Date"),
            fieldtype: "Date",
            default: frappe.datetime.get_today(),
        },
    ],

    onload(report) {
        report.page.add_inner_button(__("Submit Selected to EFRIS"), () => {
            const checked = frappe.query_report.get_checked_items();
            if (!checked.length) {
                frappe.msgprint(__("Please select at least one document."));
                return;
            }
            const names = checked.map(r => r.name);
            const doctype = frappe.query_report.get_filter_value("doctype");

            frappe.confirm(
                __(`Submit ${names.length} document(s) to EFRIS?`),
                () => {
                    frappe.call({
                        method: "efris_integration.api.bulk.bulk_submit",
                        args: { doctype, names: JSON.stringify(names) },
                        freeze: true,
                        freeze_message: __("Submitting to EFRIS…"),
                        callback(r) {
                            if (r.message) {
                                const res = r.message;
                                frappe.msgprint({
                                    title: __("Bulk Submission Complete"),
                                    message: __(
                                        `<b>Success:</b> ${res.success.length}<br>`
                                        + `<b>Failed:</b> ${res.failed.length}`
                                        + (res.failed.length
                                            ? "<br>" + res.failed.map(f => `${f.name}: ${f.error}`).join("<br>")
                                            : "")
                                    ),
                                    indicator: res.failed.length ? "orange" : "green",
                                });
                                frappe.query_report.refresh();
                            }
                        }
                    });
                }
            );
        });
    },

    formatter(value, row, column, data, default_formatter) {
        if (column.fieldname === "efris_status") {
            const colours = {
                "Submitted": "green",
                "Failed": "red",
                "Pending": "yellow",
            };
            const c = colours[value] || "gray";
            return `<span class="indicator-pill ${c}">${value || "Not Submitted"}</span>`;
        }
        return default_formatter(value, row, column, data);
    }
};
