// Copyright (c) 2024, Cuatrocubos Soluciones and contributors
// For license information, please see license.txt

frappe.ui.form.on("Plan Seguro", {
	refresh(frm) {

	},

  subscription_plan(frm) {
    frappe.call({
      method: "erpnext.accounts.doctype.subscription_plan.subscription_plan.get_plan_rate",
      args: {
        plan: frm.doc.subscription_plan
      },
      callback: function(r) {
        if (r.message) {
          console.log(r.message)
          frm.set_value("prima_mensual", r.message)
        }
      }
    })
  },
});
