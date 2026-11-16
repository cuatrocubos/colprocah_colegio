frappe.query_reports["Estado de Cuenta de Colegiado"] = {
    filters: [
        {
            fieldname: "customer",
            label: __("Customer"),
            fieldtype: "Link",
            options: "Customer",
            reqd: 1                   // mandatory — remove if you want it optional
        },
        {
            fieldname: "from_date",
            label: __("From Date"),
            fieldtype: "Date",
            reqd: 0                   // optional
        },
        {
            fieldname: "to_date",
            label: __("To Date"),
            fieldtype: "Date",
            reqd: 0                   // optional
        }
    ],

    onload: function(report) {
        // Auto-set from_date to start of current year, to_date to today
        // Remove these lines if you prefer empty defaults
        // report.set_filter_value("from_date", frappe.datetime.year_start());
        report.set_filter_value("to_date", frappe.datetime.get_today());
    },

    formatter: function(value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        if (column.fieldname === "mora" && data && data.mora > 0) {
            value = `<span style="color: red; font-weight: bold;">${value}</span>`;
        }
        if (column.fieldname === "total_pagado") {
            value = `<span style="font-weight: bold;">${value}</span>`;
        }
        return value;
    }
};