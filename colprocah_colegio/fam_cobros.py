from contextlib import contextmanager

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import flt

from colprocah_colegio.lending import lending_installed

# Cuotas de préstamos FAM (Lending) cobradas en COLPROCAH.
#
# COLPROCAH factura la cuota con el artículo PREST-FAM, ligado al préstamo de FAM, contra una cuenta por
# pagar a FAM. Cuando un Payment Entry de COLPROCAH deja la factura pagada, se crea en FAM un Loan Repayment
# por línea contra la cuenta por cobrar a COLPROCAH; los traslados de COLPROCAH a FAM liquidan ambas cuentas.
# Los préstamos anteriores a 2026 siguen con PREST-PER y el flujo de siempre.

COLPROCAH = "Colegio de Profesionales en Ciencias Agrícolas de Honduras"
FAM = "Fondo de Ayuda Mutua"
ITEM = "PREST-FAM"
TEMPLATE_ITEM = "PREST-PER"

# company: (account_number, account_name, parent_account_number, account_type)
ACCOUNTS = {
	COLPROCAH: ("20001030", "CXP FAM PRESTAMOS LENDING", "20001", "Income Account"),
	FAM: ("11301026", "CXC COLPROCAH COBROS PRESTAMOS", "11301", ""),
}

CLOSED_LOAN_STATUSES = ("Draft", "Sanctioned", "Closed", "Settled", "Written Off")


def get_account(company):
	return frappe.db.get_value("Account", {"company": company, "account_number": ACCOUNTS[company][0]})


def is_active():
	return lending_installed() and frappe.db.exists("Company", COLPROCAH) and frappe.db.exists("Company", FAM)


def get_custom_fields():
	loan_item = f"eval:doc.item_code=='{ITEM}'"
	return {
		"Sales Invoice Item": [
			{
				"fieldname": "prestamo_fam",
				"label": "Préstamo FAM",
				"fieldtype": "Link",
				"options": "Loan",
				"insert_after": "item_name",
				"depends_on": loan_item,
				"mandatory_depends_on": loan_item,
			},
			{
				"fieldname": "numero_prestamo_fam",
				"label": "Número de Préstamo",
				"fieldtype": "Data",
				"fetch_from": "prestamo_fam.numero_prestamo",
				"insert_after": "prestamo_fam",
				"depends_on": loan_item,
				"read_only": 1,
			},
		],
		"Loan Repayment": [
			{
				"fieldname": "colprocah_sales_invoice",
				"label": "Factura COLPROCAH",
				"fieldtype": "Link",
				"options": "Sales Invoice",
				"insert_after": "reference_date",
				"read_only": 1,
				"no_copy": 1,
				"search_index": 1,
			},
			{
				"fieldname": "colprocah_payment_entry",
				"label": "Pago COLPROCAH",
				"fieldtype": "Link",
				"options": "Payment Entry",
				"insert_after": "colprocah_sales_invoice",
				"read_only": 1,
				"no_copy": 1,
				"search_index": 1,
			},
			{
				"fieldname": "colprocah_invoice_item",
				"label": "Línea de Factura COLPROCAH",
				"fieldtype": "Data",
				"insert_after": "colprocah_payment_entry",
				"read_only": 1,
				"hidden": 1,
				"no_copy": 1,
				"search_index": 1,
			},
		],
	}


def setup_fam_collections():
	"""Custom fields, accounts and item; safe to run on every migrate."""
	if not is_active():
		return

	create_custom_fields(get_custom_fields(), update=True)

	for company, (number, name, parent_number, account_type) in ACCOUNTS.items():
		if get_account(company):
			continue
		parent = frappe.db.get_value("Account", {"company": company, "account_number": parent_number})
		frappe.get_doc(
			doctype="Account",
			company=company,
			account_number=number,
			account_name=name,
			parent_account=parent,
			account_type=account_type,
		).insert(ignore_permissions=True)

	if not frappe.db.exists("Item", ITEM):
		template = frappe.get_doc("Item", TEMPLATE_ITEM)
		frappe.get_doc(
			doctype="Item",
			item_code=ITEM,
			item_name="PRESTAMO PERSONAL FAM",
			description="Cuota de préstamo personal FAM (Lending)",
			item_group=template.item_group,
			stock_uom=template.stock_uom,
			is_stock_item=0,
			item_defaults=[{"company": COLPROCAH, "income_account": get_account(COLPROCAH)}],
		).insert(ignore_permissions=True)


@contextmanager
def as_lending_user():
	"""Lending checks permissions on the demands, accruals and reschedules it creates along the way, and the
	COLPROCAH staff who record payments have no Lending roles: run the FAM side as Administrator."""
	user = frappe.session.user
	frappe.set_user("Administrator")
	try:
		yield
	finally:
		frappe.set_user(user)


def validate_loan_lines(doc, method=None):
	"""Sales Invoice: every PREST-FAM line names an open FAM loan of the invoice's customer."""
	if doc.company != COLPROCAH or not is_active():
		return

	for row in doc.items:
		if row.item_code != ITEM:
			row.prestamo_fam = None
			continue
		if not row.prestamo_fam:
			frappe.throw(_("Fila {0}: indique el Préstamo FAM de la cuota {1}.").format(row.idx, ITEM))

		loan = frappe.db.get_value(
			"Loan",
			row.prestamo_fam,
			["docstatus", "company", "applicant_type", "applicant", "status"],
			as_dict=True,
		)
		if not loan or loan.docstatus != 1 or loan.company != FAM:
			frappe.throw(_("Fila {0}: {1} no es un préstamo validado de FAM.").format(row.idx, row.prestamo_fam))
		if loan.applicant_type != "Customer" or loan.applicant != doc.customer:
			frappe.throw(
				_("Fila {0}: el préstamo {1} es de {2}, no de {3}.").format(
					row.idx, row.prestamo_fam, loan.applicant, doc.customer
				)
			)
		if loan.status in CLOSED_LOAN_STATUSES and not doc.is_return:
			frappe.throw(
				_("Fila {0}: el préstamo {1} está {2}.").format(row.idx, row.prestamo_fam, _(loan.status))
			)


def get_invoices(payment_entry):
	return sorted(
		{r.reference_name for r in payment_entry.references if r.reference_doctype == "Sales Invoice"}
	)


def get_loan_lines(invoice):
	return frappe.get_all(
		"Sales Invoice Item",
		filters={"parenttype": "Sales Invoice", "parent": invoice, "item_code": ITEM},
		fields=["name", "idx", "prestamo_fam", "base_amount"],
		order_by="idx",
	)


def create_loan_repayments(doc, method=None):
	"""Payment Entry: a FAM Loan Repayment for each PREST-FAM line of the invoices it leaves fully paid."""
	if doc.company != COLPROCAH or doc.payment_type != "Receive" or not is_active():
		return

	partially_paid = []
	for invoice in get_invoices(doc):
		lines = get_loan_lines(invoice)
		if not lines:
			continue
		if flt(frappe.db.get_value("Sales Invoice", invoice, "outstanding_amount"), 2) > 0:
			partially_paid.append(invoice)
			continue
		for line in lines:
			if not frappe.db.exists("Loan Repayment", {"colprocah_invoice_item": line.name, "docstatus": 1}):
				with as_lending_user():
					make_loan_repayments(doc, invoice, line)

	if partially_paid:
		frappe.msgprint(
			_(
				"Las facturas {0} aún tienen saldo: las cuotas de préstamo FAM se registrarán en FAM cuando "
				"queden pagadas por completo."
			).format(", ".join(partially_paid)),
			indicator="orange",
			alert=True,
		)


def make_loan_repayments(payment_entry, invoice, line):
	"""The line amount is applied as is: what is due as a Normal Repayment, any excess as a Pre Payment.

	A Normal Repayment above the amount due lowers principal but leaves the schedule, so later installments
	would still charge interest on the old balance; a Pre Payment reschedules (same installment, shorter term).
	"""
	from lending.loan_management.doctype.loan_repayment.loan_repayment import calculate_amounts
	from lending.loan_management.doctype.process_loan_demand.process_loan_demand import (
		process_daily_loan_demands,
	)

	# installments due by the payment date must exist, or they'd be taken as excess
	process_daily_loan_demands(posting_date=payment_entry.posting_date, loan=line.prestamo_fam)
	due = flt(calculate_amounts(line.prestamo_fam, payment_entry.posting_date).get("payable_amount"), 2)
	amount = flt(line.base_amount, 2)

	normal = min(amount, max(due, 0))
	if normal > 0:
		make_loan_repayment(payment_entry, invoice, line, "Normal Repayment", normal)
	if flt(amount - normal, 2) > 0:
		make_loan_repayment(payment_entry, invoice, line, "Pre Payment", flt(amount - normal, 2))


def make_loan_repayment(payment_entry, invoice, line, repayment_type, amount):
	repayment = frappe.get_doc(
		doctype="Loan Repayment",
		against_loan=line.prestamo_fam,
		company=FAM,
		repayment_type=repayment_type,
		posting_date=payment_entry.posting_date,
		value_date=payment_entry.posting_date,
		amount_paid=amount,
		# not a Mode of Payment: those only accept Bank, Cash or Receivable accounts
		payment_account=get_account(FAM),
		reference_number=payment_entry.reference_no or payment_entry.name,
		reference_date=payment_entry.reference_date or payment_entry.posting_date,
		colprocah_sales_invoice=invoice,
		colprocah_payment_entry=payment_entry.name,
		colprocah_invoice_item=line.name,
	)
	repayment.insert()
	repayment.submit()
	return repayment


def cancel_loan_repayments(doc, method=None):
	"""Payment Entry cancelled: its invoices are no longer fully paid, so cancel their FAM repayments."""
	if doc.company != COLPROCAH or not is_active():
		return

	invoices = get_invoices(doc)
	or_filters = {"colprocah_payment_entry": doc.name}
	if invoices:
		or_filters["colprocah_sales_invoice"] = ("in", invoices)
	for name in frappe.get_all(
		"Loan Repayment",
		filters={"docstatus": 1},
		or_filters=or_filters,
		pluck="name",
		order_by="value_date desc, creation desc",
	):
		with as_lending_user():
			frappe.get_doc("Loan Repayment", name).cancel()
