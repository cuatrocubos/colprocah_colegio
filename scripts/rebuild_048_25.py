"""Rebuild legacy FAM loan 048/25 in dev Lending, without GL entries.

Run from the bench sites dir:
  ../env/bin/python -W ignore ../apps/colprocah_colegio/scripts/rebuild_048_25.py [--commit]
Without --commit everything is rolled back.
"""

import sys

import frappe
from frappe.utils import add_days, flt, getdate

COMMIT = "--commit" in sys.argv
SITE = "sac.colprocah.com.localhost"

LOAN = dict(
	applicant="C-2023-00552",  # JAIRO BENICIO CHAVARRIA ALVAREZ
	numero_prestamo="048/25",
	posting_date="2025-11-04",  # PD-FAM-2025-000321
	loan_amount=30000,
	rate_of_interest=17,
	repayment_periods=12,
	# Members pay late in the month (installments labelled Nov..Jun); every payment lands on/after the 25th.
	repayment_start_date="2025-11-25",
)

# (value date, amount, legacy Journal Entry) from 11301001
PAYMENTS = [
	("2025-12-01", 2737.00, "PD-FAM-2025-000405"),
	("2025-12-26", 2737.00, "PD-FAM-2026-000052-1"),
	("2026-01-28", 2736.14, "PD-FAM-2026-000085"),
	("2026-03-17", 2737.00, "PD-FAM-2026-000109"),
	("2026-03-27", 2737.00, "PD-FAM-2026-000149"),
	("2026-04-25", 2737.00, "PD-FAM-2026-000217"),
	("2026-05-27", 2737.00, "PD-FAM-2026-000237"),
	("2026-06-26", 2737.00, "PD-FAM-2026-000276"),
]

COMPANY = "Fondo de Ayuda Mutua"
PRODUCT = "Prestamo FAM Empleados"


def main():
	frappe.init(SITE, sites_path=".")
	frappe.connect()
	frappe.set_user("Administrator")

	# The balance is already booked in 11301001: suppress Lending GL for this process only.
	import lending.loan_management.controllers.loan_controller as loan_controller

	loan_controller.loan_accounting_enabled = lambda company: False

	from lending.loan_management.doctype.loan.loan import make_loan_disbursement
	from lending.loan_management.doctype.loan_repayment.loan_repayment import calculate_amounts
	from lending.loan_management.doctype.process_loan_demand.process_loan_demand import (
		process_daily_loan_demands,
	)
	from lending.loan_management.doctype.process_loan_interest_accrual.process_loan_interest_accrual import (
		process_loan_interest_accrual_for_loans,
	)

	if frappe.db.exists("Loan", {"numero_prestamo": LOAN["numero_prestamo"], "docstatus": ("<", 2)}):
		sys.exit(f"Loan {LOAN['numero_prestamo']} already exists")

	t0 = frappe.utils.now()
	try:
		loan = frappe.get_doc(
			dict(
				doctype="Loan",
				naming_series="ACC-LOAN-.YYYY.-",  # dev-only naming workaround
				applicant_type="Customer",
				company=COMPANY,
				loan_product=PRODUCT,
				repayment_method="Repay Over Number of Periods",
				**LOAN,
			)
		)
		loan.insert()
		loan.submit()

		dis = make_loan_disbursement(
			loan.name,
			posting_date=LOAN["posting_date"],
			disbursement_date=LOAN["posting_date"],
		)
		dis.set("loan_disbursement_charges", [])  # legacy 900 fee is already booked in 40003002
		dis.insert()
		dis.submit()
		print(f"{loan.name} {loan.numero_prestamo} {loan.applicant_name} disbursed {dis.name}")

		for value_date, amount, legacy_je in PAYMENTS:
			process_loan_interest_accrual_for_loans(posting_date=add_days(value_date, -1), loan=loan.name)
			process_daily_loan_demands(posting_date=value_date, loan=loan.name)

			payable = flt(calculate_amounts(loan.name, value_date).get("payable_amount"), 2)

			rep = frappe.get_doc(
				dict(
					doctype="Loan Repayment",
					against_loan=loan.name,
					loan_disbursement=dis.name,
					repayment_type="Normal Repayment",
					company=COMPANY,
					posting_date=value_date,
					value_date=value_date,
					amount_paid=amount,
					reference_number=legacy_je,
					reference_date=value_date,
				)
			)
			rep.insert()
			rep.submit()
			print(
				f"{rep.name} {value_date} due {payable:>9,.2f} paid {amount:>9,.2f}  interest {flt(rep.total_interest_paid):>7,.2f}"
				f"  penalty {flt(rep.total_penalty_paid) + flt(rep.unbooked_penalty_paid):>6,.2f}"
				f"  principal {flt(rep.principal_amount_paid):>9,.2f}  excess {flt(rep.excess_amount):>5,.2f}  ({legacy_je})"
			)

		loan.reload()
		print(
			f"Loan status {loan.status}; principal paid {loan.total_principal_paid:,.2f}; "
			f"outstanding principal {loan.disbursed_amount - loan.total_principal_paid:,.2f}"
		)
		print(f"Excess paid: {loan.excess_amount_paid:,.2f}")
		gl = frappe.db.count("GL Entry", {"creation": (">=", t0)})
		print(f"GL Entries created: {gl}")

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
