import frappe
from frappe import _
from frappe.utils import flt, getdate, today

from colprocah_colegio.fam_cobros import COLPROCAH, FAM, ITEM, get_account, is_active

# COLPROCAH's payable to FAM is shared with the legacy PREST-PER loans, so its balance can't be compared.
# Instead: PREST-FAM lines billed in COLPROCAH vs the Loan Repayments they produced in FAM; the difference
# is the lines without a repayment. FAM's receivable from COLPROCAH is what is still to be transferred.


def execute(filters=None):
	if not is_active():
		frappe.throw(_("Lending y las empresas COLPROCAH y FAM son necesarios para este reporte."))

	to_date = getdate((filters or {}).get("to_date") or today())
	billed = billed_lines(to_date)
	repaid = repayments(to_date)
	lines = lines_without_repayment(to_date)
	pending = sum(flt(line.amount) for line in lines)
	receivable = get_account(FAM)

	data = [
		summary(_("Cuotas {0} facturadas en COLPROCAH").format(ITEM), billed),
		summary(_("Repagos en FAM de cuotas cobradas en COLPROCAH"), repaid),
		summary(_("Diferencia"), billed - repaid, bold=True),
		summary(_("Cuotas {0} sin repago en FAM (detalle abajo)").format(ITEM), pending),
		summary(_("Sin explicar"), billed - repaid - pending, bold=True),
		summary(_("Pendiente de traslado de COLPROCAH a FAM ({0})").format(receivable), balance(receivable, to_date)),
	]
	if lines:
		data.append({})
		data.extend(lines)
	return get_columns(), data


def get_columns():
	return [
		{"fieldname": "concept", "label": _("Concepto"), "fieldtype": "Data", "width": 420},
		{"fieldname": "invoice", "label": _("Factura"), "fieldtype": "Link", "options": "Sales Invoice", "width": 140},
		{"fieldname": "posting_date", "label": _("Fecha"), "fieldtype": "Date", "width": 100},
		{"fieldname": "customer", "label": _("Cliente"), "fieldtype": "Link", "options": "Customer", "width": 120},
		{"fieldname": "customer_name", "label": _("Nombre"), "fieldtype": "Data", "width": 220},
		{"fieldname": "loan", "label": _("Préstamo FAM"), "fieldtype": "Link", "options": "Loan", "width": 160},
		{"fieldname": "amount", "label": _("Monto"), "fieldtype": "Currency", "width": 130},
	]


def summary(concept, amount, bold=False):
	return {"concept": concept, "amount": flt(amount, 2), "bold": int(bold)}


def balance(account, to_date):
	return flt(
		frappe.db.sql(
			"""select coalesce(sum(debit - credit), 0) from `tabGL Entry`
			where account = %s and is_cancelled = 0 and posting_date <= %s""",
			(account, to_date),
		)[0][0]
	)


def billed_lines(to_date):
	return flt(
		frappe.db.sql(
			"""select coalesce(sum(sii.base_amount), 0)
			from `tabSales Invoice Item` sii join `tabSales Invoice` si on si.name = sii.parent
			where si.docstatus = 1 and si.company = %s and sii.item_code = %s and si.posting_date <= %s""",
			(COLPROCAH, ITEM, to_date),
		)[0][0]
	)


def repayments(to_date):
	return flt(
		frappe.db.sql(
			"""select coalesce(sum(amount_paid), 0) from `tabLoan Repayment`
			where docstatus = 1 and company = %s and ifnull(colprocah_invoice_item, '') != ''
				and value_date <= %s""",
			(FAM, to_date),
		)[0][0]
	)


def lines_without_repayment(to_date):
	lines = frappe.db.sql(
		"""select si.name invoice, si.posting_date, si.customer, si.customer_name, sii.prestamo_fam loan,
			sii.base_amount amount, si.outstanding_amount, si.is_return
		from `tabSales Invoice Item` sii join `tabSales Invoice` si on si.name = sii.parent
		where si.docstatus = 1 and si.company = %(company)s and sii.item_code = %(item)s
			and si.posting_date <= %(to_date)s
			and not exists (
				select 1 from `tabLoan Repayment` lr
				where lr.colprocah_invoice_item = sii.name and lr.docstatus = 1 and lr.value_date <= %(to_date)s
			)
		order by si.posting_date, si.name""",
		{"company": COLPROCAH, "item": ITEM, "to_date": to_date},
		as_dict=True,
	)
	for line in lines:
		if line.is_return:
			line.concept = _("Nota de crédito")
		elif flt(line.outstanding_amount, 2) > 0:
			line.concept = _("Factura con saldo pendiente")
		else:
			line.concept = _("Factura pagada sin repago en FAM")
	return lines
