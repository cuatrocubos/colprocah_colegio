"""Cancel loans and all their Lending documents.

Loans rebuilt without GL (fam_legacy_loans.py build) are cancelled without trying to reverse
entries they never posted; loans whose documents did post GL get their entries reversed normally.

Run from the bench sites dir:
  ../env/bin/python -W ignore ../apps/colprocah_colegio/scripts/cancel_loan.py <loan> [<loan> ...] [--site SITE] [--commit]
Without --commit everything is rolled back.
"""

import argparse

import frappe

LINKED = [
	("Loan Repayment", "against_loan"),
	("Loan Demand", "loan"),
	("Loan Interest Accrual", "loan"),
	("Process Loan Demand", "loan"),
	("Process Loan Interest Accrual", "loan"),
	("Loan Disbursement", "against_loan"),
	("Loan Repayment Schedule", "loan"),
]


def loan_documents(loan):
	return [loan] + [
		name for doctype, field in LINKED for name in frappe.get_all(doctype, {field: loan}, pluck="name")
	]


def cancel(loan, loan_controller, accounting_enabled):
	docs = loan_documents(loan)
	has_gl = frappe.db.count("GL Entry", {"voucher_no": ("in", docs), "is_cancelled": 0}) > 0
	# Rebuilt loans never posted GL; don't try to reverse any.
	loan_controller.loan_accounting_enabled = accounting_enabled if has_gl else (lambda company: False)

	for name in frappe.get_all(
		"Loan Repayment", {"against_loan": loan, "docstatus": 1}, pluck="name", order_by="value_date desc, creation desc"
	):
		frappe.get_doc("Loan Repayment", name).cancel()

	for name in frappe.get_all("Loan Disbursement", {"against_loan": loan, "docstatus": 1}, pluck="name"):
		frappe.get_doc("Loan Disbursement", name).cancel()

	frappe.get_doc("Loan", loan).cancel()

	# Anything the cascade left behind
	for doctype, field in LINKED:
		for name in frappe.get_all(doctype, {field: loan, "docstatus": 1}, pluck="name", order_by="creation desc"):
			doc = frappe.get_doc(doctype, name)
			doc.flags.ignore_links = True
			doc.cancel()

	left = {doctype: n for doctype, field in LINKED if (n := frappe.db.count(doctype, {field: loan, "docstatus": 1}))}
	open_gl = frappe.db.count("GL Entry", {"voucher_no": ("in", docs), "is_cancelled": 0})
	print(f"{loan}: cancelled {len(docs)} documents ({'GL reversed' if has_gl else 'no GL'}); "
		f"still submitted {left or 'none'}; open GL rows {open_gl}")


def main():
	parser = argparse.ArgumentParser()
	parser.add_argument("loans", nargs="+")
	parser.add_argument("--site", default="sac.colprocah.com.localhost")
	parser.add_argument("--commit", action="store_true")
	args = parser.parse_args()

	frappe.init(args.site, sites_path=".")
	frappe.connect()
	frappe.set_user("Administrator")

	import lending.loan_management.controllers.loan_controller as loan_controller

	accounting_enabled = loan_controller.loan_accounting_enabled
	try:
		for loan in args.loans:
			if frappe.db.get_value("Loan", loan, "docstatus") != 1:
				print(f"{loan}: not submitted, skipped")
				continue
			cancel(loan, loan_controller, accounting_enabled)

		if args.commit:
			frappe.db.commit()
			print("COMMITTED")
		else:
			frappe.db.rollback()
			print("DRY RUN, rolled back")
	except BaseException:
		frappe.db.rollback()
		raise


main()
