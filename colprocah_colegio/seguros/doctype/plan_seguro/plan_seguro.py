# Copyright (c) 2024, Cuatrocubos Soluciones and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from erpnext.accounts.doctype.subscription_plan.subscription_plan import (
	get_plan_rate
)

class PlanSeguro(Document):
	def validate(self):
		validate_prima_mensual(self)

def validate_prima_mensual(self):
	if self.subscription_plan != None:
		prima_mensual_plan = get_plan_rate(self.subscription_plan)
		self.prima_mensual = prima_mensual_plan
