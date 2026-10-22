// EFRIS Item Mapping: the account is optional; it overrides the item's
// income (Sales) or expense (Purchase) account on invoices imported from URA.

frappe.ui.form.on("EFRIS Item Mapping", {
    setup(frm) {
        frm.set_query("account", () => ({
            filters: {
                company: frm.doc.company,
                is_group: 0,
                root_type: frm.doc.direction === "Sales" ? "Income" : ["in", ["Expense", "Asset"]],
            },
        }));
    },
});
