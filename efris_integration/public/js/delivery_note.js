/**
 * Delivery Note: live excise duty on the lines (see excise.py).
 * Delivery Notes are not sent to URA (the Sales Invoice is the fiscal document).
 * Mirrors: Delivery.cs
 */

frappe.ui.form.on("Delivery Note Item", efris_excise_line_events());
