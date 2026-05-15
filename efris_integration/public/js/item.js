/**
 * EFRIS fields helper for Item master.
 * Mirrors: ItemMaster.cs, ExciseDuty.cs
 *
 * When the excise flag is checked/unchecked, show/hide the excise rate fields.
 * Provide a quick lookup of EFRIS goods categories.
 */

frappe.ui.form.on("Item", {

    refresh(frm) {
        efris_item_toggle_excise(frm);
    },

    efris_excise_flag(frm) {
        efris_item_toggle_excise(frm);
    },
});

function efris_item_toggle_excise(frm) {
    const is_excisable = frm.doc.efris_excise_flag;
    frm.toggle_display("efris_excise_rate",     is_excisable);
    frm.toggle_display("efris_excise_rate_usd", is_excisable);
    frm.toggle_display("efris_weight_unit",     is_excisable);
    frm.toggle_display("volume_per_unit",       is_excisable);
}
