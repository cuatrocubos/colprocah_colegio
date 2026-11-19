frappe.query_reports["Conciliacion Prestamos FAM COLPROCAH"] = {
	filters: [
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
	],
};
