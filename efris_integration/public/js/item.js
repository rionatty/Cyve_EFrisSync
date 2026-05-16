/**
 * EFRIS / URA — Item master helper.
 * Mirrors: ItemMaster.cs (SAP B1 EFRIS integration)
 *
 * Features:
 *  • "Upload to EFRIS" button — calls T130 (Add) or T131 (Update)
 *    depending on the "Upload Type On EFRIS" field value.
 *  • Show/hide excise detail fields when "Have Excise Tax" = Yes.
 *  • Auto-sync legacy efris_excise_flag checkbox so invoice logic keeps working.
 */

frappe.ui.form.on("Item", {

    refresh(frm) {
        efris_toggle_excise(frm);
        efris_add_upload_button(frm);
    },

    efris_have_excise_tax(frm) {
        efris_toggle_excise(frm);
        // Keep the hidden legacy checkbox in sync with the new dropdown
        frm.set_value("efris_excise_flag", frm.doc.efris_have_excise_tax === "Yes" ? 1 : 0);
    },

    // Legacy path — if some code still sets the hidden checkbox directly
    efris_excise_flag(frm) {
        efris_toggle_excise(frm);
    },
});


// ─── Upload button ──────────────────────────────────────────────────────────

function efris_add_upload_button(frm) {
    // Only show the button when the item is saved and upload type is set
    if (frm.is_new()) return;

    const upload_type = frm.doc.efris_upload_type;
    if (!upload_type) return;

    const label = upload_type === "Add"
        ? "📤 Upload to EFRIS"
        : "🔄 Update on EFRIS";

    frm.add_custom_button(label, () => {
        efris_upload_item(frm);
    }, "EFRIS");
}


function efris_upload_item(frm) {
    const upload_type = frm.doc.efris_upload_type;
    if (!upload_type) {
        frappe.msgprint({
            title:   "Upload Type Not Set",
            message: "Please set <b>Upload Type On EFRIS</b> to <b>Add</b> or <b>Update</b> first.",
            indicator: "orange",
        });
        return;
    }

    const company = frappe.defaults.get_default("company");
    if (!company) {
        frappe.msgprint({
            title:   "Company Not Set",
            message: "No default company found. Please set a default company.",
            indicator: "orange",
        });
        return;
    }

    const action = upload_type === "Add" ? "register" : "update";
    frappe.confirm(
        `${upload_type} item <b>${frm.doc.item_code}</b> on EFRIS (T${upload_type === "Add" ? "130" : "131"})?`,
        () => {
            frappe.show_progress("Uploading to EFRIS…", 0, 100, "Please wait");

            frappe.call({
                method:  "efris_integration.api.goods.upload_item",
                args: {
                    item_code: frm.doc.name,
                    company:   company,
                },
                freeze:         true,
                freeze_message: `${upload_type === "Add" ? "Registering" : "Updating"} item on EFRIS…`,
                callback(r) {
                    frappe.hide_progress();
                    if (r.exc) return;   // frappe already shows the server error

                    const data = r.message || {};
                    if (data.status === "ok") {
                        // If Add returned a goodsId, refresh so Reference No. shows it
                        if (data.goods_id) {
                            frm.reload_doc();
                        }
                    }
                },
                error() {
                    frappe.hide_progress();
                },
            });
        }
    );
}


// ─── Excise field visibility ────────────────────────────────────────────────

function efris_toggle_excise(frm) {
    const is_excisable = (
        frm.doc.efris_have_excise_tax === "Yes" ||
        frm.doc.efris_excise_flag == 1
    );

    const excise_fields = [
        "efris_excise_duty_category",
        "efris_piece_uom",
        "efris_piece_unit_price",
        "efris_excise_weight",
        "efris_piece_scaled_value",
        "efris_excise_rate",
        "efris_excise_rate_usd",
        "efris_weight_unit",
        "volume_per_unit",
    ];

    excise_fields.forEach(f => frm.toggle_display(f, is_excisable));
}
