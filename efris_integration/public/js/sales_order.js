/**
 * Sales Order: live excise duty on the lines (see excise.py).
 * Sales Orders are not sent to URA (the Sales Invoice is the fiscal document).
 * Mirrors: SalesOrder.cs
 */

frappe.ui.form.on("Sales Order Item", efris_excise_line_events());
