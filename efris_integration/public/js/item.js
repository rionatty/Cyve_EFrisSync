/**
 * EFRIS fields helper for Item master.
 * Mirrors: ItemMaster.cs, ExciseDuty.cs
 *
 * Excise fields (right column) are shown only when "Have Excise Tax" = Yes.
 * The legacy efris_excise_flag checkbox is kept hidden and synced automatically
 * so existing invoice submission logic continues to work without changes.
 */

frappe.ui.form.on("Item", {

    refresh(frm) {
        efris_toggle_excise(frm);
    },

    efris_have_excise_tax(frm) {
        efris_toggle_excise(frm);
        // Keep legacy hidden checkbox in sync
        frm.set_value("efris_excise_flag", frm.doc.efris_have_excise_tax === "Yes" ? 1 : 0);
    },

    // Legacy path: if someone sets the hidden checkbox directly
    efris_excise_flag(frm) {
        efris_toggle_excise(frm);
    },
});


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
