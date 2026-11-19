"""Cancel a rebuilt (no-GL) loan and all its Lending documents.

Run from the bench sites dir:
  ../env/bin/python -W ignore ../apps/colprocah_colegio/scripts/cancel_loan.py <loan> [--commit]
Without --commit everything is rolled back.
"""

import sys

import frappe

LOAN = sys.argv[1]
COMMIT = "--commit" in sys.argv
LINKED = [
	("Loan Repayment", "against_loan"),
	("Loan Demand", "loan"),
	("Loan Interest Accrual", "loan"),
	("Process Loan Demand", "loan"),
	("Process Loan Interest Accrual", "loan"),
	("Loan Disbursement", "against_loan"),
	("Loan Repayment Schedule", "loan"),
]


def main():
	frappe.init("sac.colprocah.com.localhost", sites_path=".")
	frappe.connect()
	frappe.set_user("Administrator")

	if frappe.db.get_value("Loan", LOAN, "docstatus") != 1:
		sys.exit(f"Loan {LOAN} is not submitted")

	# These documents were created without GL; don't try to reverse any.
	import lending.loan_management.controllers.loan_controller as loan_controller

	loan_controller.loan_accounting_enabled = lambda company: False

	try:
		for name in frappe.get_all(
			"Loan Repayment", {"against_loan": LOAN, "docstatus": 1}, pluck="name", order_by="value_date desc, creation desc"
		):
			frappe.get_doc("Loan Repayment", name).cancel()

		for name in frappe.get_all("Loan Disbursement", {"against_loan": LOAN, "docstatus": 1}, pluck="name"):
			frappe.get_doc("Loan Disbursement", name).cancel()

		frappe.get_doc("Loan", LOAN).cancel()

		# Anything the cascade left behind
		for doctype, field in LINKED:
			for name in frappe.get_all(doctype, {field: LOAN, "docstatus": 1}, pluck="name", order_by="creation desc"):
				doc = frappe.get_doc(doctype, name)
				doc.flags.ignore_links = True
				doc.cancel()

		for doctype, field in [("Loan", "name"), *LINKED]:
			left = frappe.get_all(doctype, {field: LOAN, "docstatus": 1}, pluck="name")
			print(f"{doctype}: {frappe.db.count(doctype, {field: LOAN})} total, still submitted {left}")
		print("GL rows for loan docs:", frappe.db.count("GL Entry", {"voucher_no": LOAN}))

		if COMMIT:
			frappe.db.commit()
			print("COMMITTED")
		else:
			frappe.db.rollback()
			print("DRY RUN, rolled back")
	except Exception:
		frappe.db.rollback()
		raise


main()
