// Ported from a production patch to erpnext/selling/doctype/customer/customer_list.js.
// Extends ERPNext's Customer list settings instead of replacing them.
frappe.listview_settings["Customer"] = frappe.listview_settings["Customer"] || {};

(function (settings) {
	const extra_fields = ["condicion", "disabled"];
	settings.add_fields = Array.from(new Set([...(settings.add_fields || []), ...extra_fields]));

	settings.get_indicator = function (doc) {
		if (doc.condicion == "Activo") {
			return [__("Activo"), "green", "condicion,=,Activo"];
		} else if (doc.condicion == "Inactivo") {
			return [__("Inactivo"), "red", "condicion,=,Inactivo"];
		} else {
			return [doc.condicion, "blue", `condicion,=,${doc.condicion}`];
		}
	};
})(frappe.listview_settings["Customer"]);
