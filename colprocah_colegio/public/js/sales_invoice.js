frappe.ui.form.on("Sales Invoice", {
	setup(frm) {
		// Préstamo FAM on PREST-FAM lines: open FAM loans of the invoice's customer
		frm.set_query("prestamo_fam", "items", () => ({
			filters: {
				company: "Fondo de Ayuda Mutua",
				applicant_type: "Customer",
				applicant: frm.doc.customer,
				docstatus: 1,
				status: ["not in", ["Draft", "Sanctioned", "Closed", "Settled", "Written Off"]],
			},
		}));
	},
});
