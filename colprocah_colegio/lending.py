import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter

# Lending is optional on the sites this app runs on, so these fields are created
# from code (guarded) instead of Custom Field fixtures, which would break migrate.

LOAN_DOCTYPES = ("Loan", "Loan Application")
LOAN_CHILD_DOCTYPES = ("Loan Disbursement", "Loan Repayment")

APPLICANT_NAME_FIELD = {"Customer": "customer_name", "Employee": "employee_name"}


def lending_installed():
	return frappe.db.exists("DocType", "Loan")


def get_custom_fields():
	codigo = {
		"fieldname": "codigo_colegiado",
		"label": "Código Colegiación",
		"fieldtype": "Data",
		"read_only": 1,
		"in_list_view": 1,
		"in_standard_filter": 1,
		"in_global_search": 1,
		"search_index": 1,
		"no_copy": 1,
	}

	numero = {
		"fieldname": "numero_prestamo",
		"label": "Número de Préstamo",
		"fieldtype": "Data",
		"description": "Número del préstamo en los archivos de Excel, ej. 001/26",
		"insert_after": "codigo_colegiado",
		"allow_on_submit": 1,
		"in_list_view": 1,
		"in_standard_filter": 1,
		"in_global_search": 1,
		"search_index": 1,
		"no_copy": 1,
	}

	fields = {dt: [{**codigo, "insert_after": "applicant_name"}] for dt in LOAN_DOCTYPES}
	fields["Loan"].append(numero)
	for dt in LOAN_CHILD_DOCTYPES:
		fields[dt] = [
			{
				"fieldname": "applicant_name",
				"label": "Applicant Name",
				"fieldtype": "Data",
				"fetch_from": "against_loan.applicant_name",
				"insert_after": "applicant",
				"read_only": 1,
				"in_list_view": 1,
				"in_standard_filter": 1,
				"in_global_search": 1,
				"no_copy": 1,
			},
			{
				**codigo,
				"fetch_from": "against_loan.codigo_colegiado",
				"insert_after": "applicant_name",
			},
		]
	fields["Loan Disbursement"].append(
		{
			**numero,
			"description": None,
			"fetch_from": "against_loan.numero_prestamo",
			"read_only": 1,
		}
	)
	return fields


def setup_loan_fields():
	if not lending_installed():
		return

	create_custom_fields(get_custom_fields(), update=True)

	for dt in LOAN_DOCTYPES:
		for prop in ("in_list_view", "in_standard_filter", "in_global_search"):
			make_property_setter(dt, "applicant_name", prop, 1, "Check", validate_fields_for_doctype=False)

	for dt in (*LOAN_DOCTYPES, *LOAN_CHILD_DOCTYPES):
		search_fields = [f.strip() for f in (frappe.get_meta(dt).search_fields or "").split(",") if f.strip()]
		wanted = ["applicant", "applicant_name", "codigo_colegiado"]
		if frappe.get_meta(dt).has_field("numero_prestamo"):
			wanted.append("numero_prestamo")
		missing = [f for f in wanted if f not in search_fields]
		if missing:
			make_property_setter(
				dt,
				None,
				"search_fields",
				",".join(search_fields + missing),
				"Data",
				for_doctype=True,
				validate_fields_for_doctype=False,
			)


def set_applicant_details(doc, method=None):
	"""Lending only fills applicant_name client-side, and only for Employee/Member applicants.

	On Loan Application the name is user-editable (it seeds new Customers), so only fill it when empty.
	"""
	name_field = APPLICANT_NAME_FIELD.get(doc.applicant_type)
	if not (doc.applicant and name_field):
		return

	fields = [name_field]
	if frappe.get_meta(doc.applicant_type).has_field("codigo_colegiado"):
		fields.append("codigo_colegiado")

	values = frappe.db.get_value(doc.applicant_type, doc.applicant, fields, as_dict=True) or {}
	if doc.doctype == "Loan" or not doc.applicant_name:
		doc.applicant_name = values.get(name_field)
	doc.codigo_colegiado = values.get("codigo_colegiado")


def validate_numero_prestamo(doc, method=None):
	doc.numero_prestamo = (doc.numero_prestamo or "").strip() or None
	if not doc.numero_prestamo:
		return

	duplicate = frappe.db.get_value(
		"Loan",
		{"numero_prestamo": doc.numero_prestamo, "docstatus": ("!=", 2), "name": ("!=", doc.name)},
	)
	if duplicate:
		frappe.throw(
			_("Número de Préstamo {0} ya está asignado al préstamo {1}").format(
				frappe.bold(doc.numero_prestamo), frappe.bold(duplicate)
			)
		)


def sync_numero_prestamo(doc, method=None):
	"""numero_prestamo can be edited after submit; push it to the loan's disbursements."""
	frappe.db.set_value(
		"Loan Disbursement",
		{"against_loan": doc.name},
		"numero_prestamo",
		doc.numero_prestamo,
		update_modified=False,
	)


def backfill_applicant_details():
	"""Fill applicant_name / codigo_colegiado on existing loans and applications (including submitted ones)."""
	if not lending_installed():
		return

	# raw SQL: frappe.qb drops the table prefix in UPDATE ... JOIN ... SET
	# keep names already typed on Loan Application; Loan names were never filled by Lending
	for dt in LOAN_DOCTYPES:
		for applicant_type, name_field in APPLICANT_NAME_FIELD.items():
			codigo = (
				", l.codigo_colegiado = a.codigo_colegiado"
				if frappe.get_meta(applicant_type).has_field("codigo_colegiado")
				else ""
			)
			frappe.db.sql(
				f"""update `tab{dt}` l join `tab{applicant_type}` a on a.name = l.applicant
				set l.applicant_name = coalesce(nullif(l.applicant_name, ''), a.`{name_field}`){codigo}
				where l.applicant_type = %s""",
				applicant_type,
			)

	for dt in LOAN_CHILD_DOCTYPES:
		frappe.db.sql(
			f"""update `tab{dt}` c join `tabLoan` l on l.name = c.against_loan
			set c.applicant_name = l.applicant_name, c.codigo_colegiado = l.codigo_colegiado"""
		)

	frappe.db.sql(
		"""update `tabLoan Disbursement` c join `tabLoan` l on l.name = c.against_loan
		set c.numero_prestamo = l.numero_prestamo"""
	)
