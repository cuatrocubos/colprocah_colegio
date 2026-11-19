"""Fix the customer records of COLPROCAH staff who have FAM personal loans.

Their loans name them in full ("CELENA YASMIN RODAS LAINEZ") but the customers were created twice
with short names, or not at all, so fam_legacy_loans.py can't match them.

Run from the bench sites dir:
  ../env/bin/python -W ignore ../apps/colprocah_colegio/scripts/fix_fam_loan_customers.py [--site SITE] [--commit]
Without --commit everything is rolled back.
"""

import argparse

import frappe
from frappe.model.rename_doc import rename_doc

# full name -> (customer to keep, duplicates to merge into it); keep=None creates the customer
STAFF = {
	"CELENA YASMIN RODAS LAINEZ": ("Celena Rodas", ["C-2023-01051", "C-2024-00049"]),
	"DORA LETICIA CERRATO": ("Dora Cerrato", ["C-2023-06715", "C-2024-00054"]),
	"DUNIA ALEJANDRA SERVELLON": ("Dunia Servellon", ["C-2023-06716", "C-2024-00051"]),
	"DINA MARLENY OSORIO": (None, []),
	"ERIKA SUSANA BONILLA": (None, []),
}


def fix(full_name, short_name, names):
	if frappe.db.exists("Customer", {"customer_name": full_name}):
		print(f"{full_name}: already exists")
		return
	existing = [n for n in names if frappe.db.exists("Customer", n)]
	if not existing:
		customer = frappe.get_doc(
			doctype="Customer",
			customer_name=full_name,
			customer_type="Individual",
			customer_group="Individual",
			territory=frappe.db.get_single_value("Selling Settings", "territory") or "Todos los territorios",
		).insert()
		print(f"{full_name}: created {customer.name}")
		return

	keep, duplicates = existing[0], existing[1:]
	for name in duplicates:
		if frappe.db.get_value("Customer", name, "customer_name") != short_name:
			raise SystemExit(f"{name} is not '{short_name}', not merging")
		rename_doc("Customer", name, keep, merge=True, show_alert=False)
		print(f"{full_name}: merged {name} into {keep}")
	frappe.db.set_value("Customer", keep, "customer_name", full_name)
	print(f"{full_name}: {keep} renamed from '{short_name}'")


def main():
	parser = argparse.ArgumentParser()
	parser.add_argument("--site", default="sac.colprocah.com.localhost")
	parser.add_argument("--commit", action="store_true")
	args = parser.parse_args()

	frappe.init(args.site, sites_path=".")
	frappe.connect()
	frappe.set_user("Administrator")
	try:
		for full_name, (short_name, names) in STAFF.items():
			fix(full_name, short_name, names)
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
