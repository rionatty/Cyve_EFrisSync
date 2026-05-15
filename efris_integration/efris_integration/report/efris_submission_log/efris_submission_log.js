frappe.query_reports["EFRIS Submission Log"] = {
    filters: [
        {
            fieldname: "interface_code",
            label: __("Interface Code"),
            fieldtype: "Select",
            options: "\nT101\nT102\nT103\nT104\nT106\nT108\nT109\nT110\nT111\nT114\nT115\nT119\nT120\nT126",
        },
        {
            fieldname: "status",
            label: __("Status"),
            fieldtype: "Select",
            options: "\nSuccess\nFailed\nPending",
        },
        {
            fieldname: "from_date",
            label: __("From"),
            fieldtype: "Date",
            default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
        },
        {
            fieldname: "to_date",
            label: __("To"),
            fieldtype: "Date",
            default: frappe.datetime.get_today(),
        },
    ],

    formatter(value, row, column, data, default_formatter) {
        if (column.fieldname === "status") {
            const c = { Success: "green", Failed: "red", Pending: "yellow" }[value] || "gray";
            return `<span class="indicator-pill ${c}">${value}</span>`;
        }
        return default_formatter(value, row, column, data);
    }
};
