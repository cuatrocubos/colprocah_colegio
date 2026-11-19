"""Rebuild FAM legacy personal loans from the books.

  report   (default) read-only: per-loan reconstruction and why a loan can't be rebuilt
  build    create the rebuildable loans in Lending (Loan, Disbursement, Repayments) without GL
  reclass  draft Journal Entry moving their balances from 11301001 to 11301022 (--submit to submit)

build/reclass roll back unless --commit.

Sources:
  * FAM  11301001 PRESTAMOS PERSONALES journal entries: disbursements (with loan number,
    member and refinanced loans), refinance payoffs, direct payments, refunds.
  * COLPROCAH Sales Invoices with item PREST-PER (monthly installment billed to the member),
    paid through Payment Entries.

Run from the bench sites dir:
  ../env/bin/python -W ignore ../apps/colprocah_colegio/scripts/fam_legacy_loans.py \
      [report|build|reclass] [--site SITE] [--cutover 2026-06-30] [--out DIR] [--only 048/25 ...] [--commit] [--submit]

Writes loans.csv, events.csv, unmatched.csv (and build.csv) to --out and prints a summary.
"""

import argparse
import csv
import json
import math
import os
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date

import frappe
from frappe.utils import add_days, add_months

SITE ="sac.colprocah.com.localhost"
FAM = "Fondo de Ayuda Mutua"
LOAN_ACCOUNT = "11301001 - PRESTAMOS PERSONALES - FAM"
LENDING_ACCOUNT = "11301022 - PRESTAMOS PERSONALES - FAM"
INTEREST_ACCOUNT = "40001005 - INTERESES S/PRESTAMOS - FAM"
PRODUCT = "Prestamo FAM Empleados"
FEE_ACCOUNT_PREFIX = "40003002"
INTEREST_ACCOUNT_PREFIX = "40001005"

NUM = r"(\d{1,4})\s*[/-]\s*(\d{2,4})"
RE_NEW_LOAN = [
	re.compile(r"PREST-\d{3}-" + NUM),  # AU-RES-PREST-252-069/23
	re.compile(r"PREST\s*[-.#]?\s*" + NUM),  # PREST-048/25, PREST045/25, PREST.040/25
	re.compile(r"AU-RES-\d+-" + NUM),  # AU-RES-239-056/2023
]
RE_CANCELLED = re.compile(
	r"CANCE\s*LACION\s+(?:TOTAL\s+)?(?:DEL?\s+)?(?:PRESTAMO\s+)?(?:PERSONAL\s+)?(?:FAM\s+)?#?\s*" + NUM
)
RE_ANY_LOAN = re.compile(r"(?:PRESTAMO|PTMO|PTAMO|PTAMOS)\.?\s*(?:FAM\s*)?#?\s*" + NUM)
RE_CODIGO = re.compile(r"(?:CARNET|COLEGIADO|COLEG)\s*[:.#]?\s*#?\s*0*(\d{3,5})")
NAME_NOISE = {
	"TRANSF", "TRANF", "TRANS", "REFER", "REF", "CHQ", "COLEG", "COLPROCAH", "PARTIDA", "COMENTARIO",
	"NOTA", "DE", "DEL", "LA", "Y", "PAGO", "POR",
}


def norm(text):
	text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
	return re.sub(r"\s+", " ", text.upper().replace("\n", " ")).strip()


def loan_no(num, year):
	year = int(year) % 100
	return f"{int(num):03d}/{year:02d}"


def new_loan_number(text):
	text = RE_CANCELLED.sub(" ", text)
	for regex in RE_NEW_LOAN:
		if m := regex.search(text):
			return loan_no(*m.groups())


def cancelled_numbers(text):
	return [loan_no(*m.groups()) for m in RE_CANCELLED.finditer(text)]


def any_loan_numbers(text):
	return [loan_no(*m.groups()) for m in RE_ANY_LOAN.finditer(text)]


def name_tokens(text):
	"""Member name tokens from the start of a remark (before PRESTAMO/PAGO)."""
	text = re.sub(r"^NOTA:\s*", "", text)
	text = re.sub(r"PARTIDA:\s*\S+\s*COMENTARIO:\s*", "", text)
	text = re.sub(r"COLPROCAH\s*/\s*", "", text)  # "COLPROCAH/JOSE RAMON SANTOS ..."
	head = re.split(r"\bPRESTAMO|\bPAGO|\bPRESTAMO|/|,|\bAU-RES", text)[0]
	return [t for t in re.findall(r"[A-Z]{2,}", head) if t not in NAME_NOISE]


class Customers:
	def __init__(self):
		rows = frappe.db.sql("select name, customer_name, codigo_colegiado from tabCustomer", as_dict=True)
		self.by_codigo = {}
		self.tokens = {}
		for r in rows:
			if r.codigo_colegiado and r.codigo_colegiado.strip().isdigit():
				self.by_codigo[int(r.codigo_colegiado)] = r.name
			self.tokens[r.name] = set(re.findall(r"[A-Z]{2,}", norm(r.customer_name)))
		self.names = {r.name: r.customer_name for r in rows}

	def match(self, text, prefer=()):
		"""(customer, how). Ties on name are broken by `prefer` (e.g. members with loan invoices)."""
		if m := RE_CODIGO.search(text):
			if customer := self.by_codigo.get(int(m.group(1))):
				return customer, "codigo"
		tokens = set(name_tokens(text))
		if len(tokens) < 2:
			return None, "no-name"
		scored = [(len(tokens & ct), c) for c, ct in self.tokens.items() if len(tokens & ct) >= 2]
		if not scored:
			return None, "no-match"
		top = max(s for s, _ in scored)
		best = [c for s, c in scored if s == top]
		if len(best) > 1:
			preferred = [c for c in best if c in prefer]
			if len(preferred) == 1:
				return preferred[0], "name+preferred"
			return None, "ambiguous"
		return best[0], "name"


def pmt(principal, annual_rate, n):
	r = annual_rate / 1200
	return principal * r / (1 - (1 + r) ** -n)


def guess_periods(principal, installment):
	"""Periods at 17% closest to an installment amount."""
	return min(range(3, 121), key=lambda n: abs(pmt(principal, 17, n) - installment))


def infer_terms(principal, installment):
	"""(annual rate, periods) whose French installment matches, preferring 17%."""
	best = None
	for rate in [17] + [x for x in range(10, 31) if x != 17]:
		for n in range(3, 121):
			diff = abs(pmt(principal, rate, n) - installment)
			if diff <= 1.0 and (best is None or diff < best[2] - 0.5):
				best = (rate, n, diff)
		if best and best[0] == 17:
			break
	return best


def load_fam():
	rows = frappe.db.sql(
		"""select name, posting_date, voucher_no, debit, credit, remarks
		from `tabGL Entry` where is_cancelled=0 and account=%s order by posting_date, creation""",
		LOAN_ACCOUNT,
		as_dict=True,
	)
	vouchers = list({r.voucher_no for r in rows})
	lines = defaultdict(list)
	for l in frappe.db.sql(
		"""select voucher_no, account, debit, credit from `tabGL Entry`
		where is_cancelled=0 and voucher_no in %s""",
		[vouchers],
		as_dict=True,
	):
		lines[l.voucher_no].append(l)
	return rows, lines


def load_colprocah():
	invoices = frappe.db.sql(
		"""select si.name, si.posting_date, si.customer, sum(sii.amount) amount
		from `tabSales Invoice Item` sii join `tabSales Invoice` si on si.name=sii.parent
		where si.docstatus=1 and sii.item_code='PREST-PER' group by si.name""",
		as_dict=True,
	)
	paid = {}
	if invoices:
		for p in frappe.db.sql(
			"""select against_voucher_no inv, max(posting_date) paid_on from `tabPayment Ledger Entry`
			where delinked=0 and voucher_no!=against_voucher_no and against_voucher_no in %s
			group by against_voucher_no""",
			[[i.name for i in invoices]],
			as_dict=True,
		):
			paid[p.inv] = p.paid_on
	for i in invoices:
		i.paid_on = paid.get(i.name)
	return invoices


def build(cutover):
	customers = Customers()
	rows, lines = load_fam()
	invoices = load_colprocah()
	invoiced = {i.customer for i in invoices}
	loans = {}
	events = []  # (loan, date, kind, amount, source, remark)
	unmatched = []
	disb_vouchers = set()

	# 1. Disbursements
	for r in rows:
		text = norm(r.remarks)
		if r.debit <= 0 or "DEVOLUCION" in text:
			continue
		number = new_loan_number(text)
		if not number:
			continue
		refinances = cancelled_numbers(text)
		owners = {l.customer for l in loans.values() if l.number in refinances and l.customer}
		customer, how = customers.match(text, prefer=owners or invoiced)
		other = lines[r.voucher_no]
		fee = sum(l.credit for l in other if l.account.startswith(FEE_ACCOUNT_PREFIX))
		key = number
		if key in loans:  # same number reused: keep both, suffix the later one
			key = f"{number}#{r.voucher_no}"
		loans[key] = frappe._dict(
			loan=key,
			number=number,
			customer=customer,
			customer_name=customers.names.get(customer, ""),
			match=how,
			disbursed_on=r.posting_date,
			amount=r.debit,
			fee=fee,
			voucher=r.voucher_no,
			refinances=refinances,
			remark=text[:200],
		)
		disb_vouchers.add(r.voucher_no)
		events.append((key, r.posting_date, "disbursement", r.debit, r.voucher_no, text[:120]))

	by_number = defaultdict(list)
	for l in loans.values():
		by_number[l.number].append(l)

	def resolve(number, on_date, customer=None):
		cands = [l for l in by_number.get(number, []) if l.disbursed_on <= on_date]
		if customer:
			cands = [l for l in cands if l.customer == customer] or cands
		return cands[-1] if cands else None

	# 2. Other 11301001 movements
	for r in rows:
		text = norm(r.remarks)
		if r.voucher_no in disb_vouchers and r.debit > 0 and "DEVOLUCION" not in text:
			continue
		other = lines[r.voucher_no]
		if r.debit > 0 and any(l.account.startswith(INTEREST_ACCOUNT_PREFIX) for l in other):
			events.append((None, r.posting_date, "interest-yearend", r.debit, r.voucher_no, text[:120]))
			continue

		if r.voucher_no in disb_vouchers and r.credit > 0:
			# payoff of the refinanced loan(s) inside the new loan's voucher
			refs = cancelled_numbers(text)
			new = next(l for l in loans.values() if l.voucher == r.voucher_no)
			if len(refs) == 1 and (loan := resolve(refs[0], r.posting_date, new.customer)):
				events.append((loan.loan, r.posting_date, "payoff-refinance", r.credit, r.voucher_no, text[:120]))
				continue
			# no usable loan number (missing, or garbled like "00/25"): the member's previous loan,
			# unless it names a loan from before 2023, which isn't rebuilt
			previous = [
				l for l in loans.values()
				if new.customer and l.customer == new.customer and l.disbursed_on < new.disbursed_on
			]
			if previous and all(int(ref[-2:]) >= 23 for ref in refs):
				loan = previous[-1]
				events.append((loan.loan, r.posting_date, "payoff-refinance?by-member", r.credit, r.voucher_no, text[:120]))
			else:
				kind = "payoff of pre-2023 loan " + ",".join(refs) if refs else "payoff, previous loan unknown"
				unmatched.append((r.posting_date, r.voucher_no, -r.credit, kind, text[:160]))
			continue

		numbers = [n for n in any_loan_numbers(text)] or cancelled_numbers(text)
		customer, _ = customers.match(text, prefer=invoiced)
		if r.debit > 0 and "DEVOLUCION" not in text:
			unmatched.append((r.posting_date, r.voucher_no, r.debit, "other debit", text[:160]))
			continue
		kind = "refund" if r.debit > 0 else ("payoff" if "CANCELACION" in text else "payment")
		amount = r.debit if r.debit > 0 else r.credit
		loan = resolve(numbers[0], r.posting_date, customer) if numbers else None
		# a movement naming a loan from before 2023 belongs to that loan, not the member's current one
		if not loan and customer and not (numbers and int(numbers[0][-2:]) < 23):
			active = [l for l in loans.values() if l.customer == customer and l.disbursed_on <= r.posting_date]
			if len(active) >= 1:
				loan = active[-1]
				kind += "?by-member"
		if loan:
			events.append((loan.loan, r.posting_date, kind, amount, r.voucher_no, text[:120]))
		else:
			unmatched.append((r.posting_date, r.voucher_no, r.debit - r.credit, kind, text[:160]))

	# 3. COLPROCAH invoices -> member's loan open at invoice date
	payoff_dates = defaultdict(list)
	for e in events:
		if e[0] and e[2].startswith("payoff"):
			payoff_dates[e[0]].append(e[1])
	refinanced_dates = {}
	for l in loans.values():
		for ref in l.refinances:
			if old := resolve(ref, l.disbursed_on, l.customer):
				refinanced_dates[old.loan] = l.disbursed_on

	def refinanced_on(loan, on_date):
		closed = [d for d in payoff_dates.get(loan.loan, []) + [refinanced_dates.get(loan.loan)] if d]
		return any(d < on_date for d in closed)

	member_loans = defaultdict(list)
	for l in loans.values():
		if l.customer:
			member_loans[l.customer].append(l)
	for ml in member_loans.values():
		ml.sort(key=lambda l: l.disbursed_on)
	for inv in invoices:
		cands = [l for l in member_loans.get(inv.customer, []) if l.disbursed_on <= inv.posting_date]
		if not cands:
			unmatched.append((inv.posting_date, inv.name, -inv.amount, "colprocah invoice, no loan", inv.customer))
			continue
		# open loans of the member at invoice date; the one whose installment fits the amount wins
		open_ = [l for l in cands if not refinanced_on(l, inv.posting_date)] or cands[-1:]
		loan = min(open_, key=lambda l: (abs(pmt(l.amount, 17, guess_periods(l.amount, inv.amount)) - inv.amount), -l.disbursed_on.toordinal()))
		events.append((loan.loan, inv.paid_on or inv.posting_date, "colprocah", inv.amount, inv.name, f"billed {inv.posting_date}"))

	# 4. Per-loan schedule replay
	per_loan = defaultdict(list)
	for e in events:
		if e[0]:
			per_loan[e[0]].append(e)
	refinanced_by = {}
	for l in loans.values():
		for ref in l.refinances:
			if old := resolve(ref, l.disbursed_on, l.customer):
				refinanced_by[old.loan] = l.loan

	for l in loans.values():
		evs = sorted(per_loan[l.loan], key=lambda e: e[1])
		pays = [e for e in evs if e[2].startswith(("payment", "colprocah"))]
		amounts = Counter(round(e[3], 2) for e in pays)
		installment = amounts.most_common(1)[0][0] if amounts else None
		terms = infer_terms(l.amount, installment) if installment else None
		l.installment = installment
		l.rate, l.periods = (terms[0], terms[1]) if terms else (None, None)
		l.refinanced_by = refinanced_by.get(l.loan)
		closed = [e for e in evs if e[2].startswith(("payoff",))]
		l.closed_on = closed[-1][1] if closed else None
		l.n_payments = len(pays)
		l.paid_total = round(sum(e[3] for e in pays), 2)
		l.refunds = round(sum(e[3] for e in evs if e[2].startswith("refund")), 2)

		# replay until cutover: interest = balance * rate/12, rest to principal
		rate = (l.rate or 17) / 1200
		bal = l.amount
		interest_by_year = defaultdict(float)
		for e in evs:
			if e[1] > cutover:
				break
			if e[2].startswith(("payment", "colprocah")):
				i = round(bal * rate, 2) if bal > 0 else 0
				interest_by_year[e[1].year] += i
				bal = round(bal - (e[3] - i), 2)
			elif e[2].startswith("refund"):
				bal = round(bal + e[3], 2)
			elif e[2].startswith("payoff"):
				bal = 0
		l.closed_by_cutover = bool(l.closed_on and l.closed_on <= cutover) or (
			l.refinanced_by and loans[l.refinanced_by].disbursed_on <= cutover
		)
		l.balance_at_cutover = 0 if l.closed_by_cutover or l.disbursed_on > cutover else max(bal, 0)
		l.overpaid = -bal if bal < 0 else 0
		l.interest_by_year = {y: round(v, 2) for y, v in sorted(interest_by_year.items())}
		l.payments = [(e[1], e[3], e[4]) for e in pays if e[1] <= cutover]
		l.skip_reason = skip_reason(l, cutover)

	return loans, events, unmatched, rows


def skip_reason(loan, cutover):
	"""Why a loan can't be rebuilt from the books; empty when it can."""
	if loan.balance_at_cutover <= 0:
		return "closed or not yet disbursed"
	if not loan.payments:
		return "no payments found"
	if loan.rate != 17:
		return f"terms unknown (installment {loan.installment})"
	if loan.balance_at_cutover <= loan.amount * 0.01 or len(loan.payments) >= loan.periods:
		return "paid off"
	if (loan.payments[0][0] - loan.disbursed_on).days > 70:
		return f"first payment {loan.payments[0][0]}: earlier payments missing"
	months = (cutover.year - loan.disbursed_on.year) * 12 + cutover.month - loan.disbursed_on.month
	expected = min(loan.periods, months)
	if len(loan.payments) < expected - 2:
		return f"{len(loan.payments)} payments for {expected} installments due"
	return ""


def first_due_date(loan):
	"""Installment 1 due date that puts the most payments on/after their due date, then the least late."""
	best = None
	for start in (loan.disbursed_on.replace(day=1), add_months(loan.disbursed_on.replace(day=1), 1)):
		for day in range(1, 29):
			first = start.replace(day=day)
			if (first - loan.disbursed_on).days < 15:
				continue
			early = late = 0
			for k, (paid_on, _amount, _source) in enumerate(loan.payments):
				delta = (paid_on - add_months(first, k)).days
				if delta < 0:
					early += 1
				else:
					late += delta
			if best is None or (early, late) < best[0]:
				best = ((early, late), first)
	return best[1], best[0][0]


def suppress_loan_gl():
	# The history is already booked in 11301001: Lending documents must not post GL.
	import lending.loan_management.controllers.loan_controller as loan_controller

	loan_controller.loan_accounting_enabled = lambda company: False


def build_loans(loans, only, out):
	from lending.loan_management.doctype.loan.loan import make_loan_disbursement
	from lending.loan_management.doctype.process_loan_demand.process_loan_demand import (
		process_daily_loan_demands,
	)
	from lending.loan_management.doctype.process_loan_interest_accrual.process_loan_interest_accrual import (
		process_loan_interest_accrual_for_loans,
	)

	suppress_loan_gl()
	if frappe.get_cached_value("Loan Product", PRODUCT, "validate_normal_repayment"):
		raise SystemExit(f"Loan Product {PRODUCT}: turn off validate_normal_repayment (members overpay by rounding)")

	meta = frappe.get_meta("Loan")
	set_series = not meta.has_field("naming_series") and (meta.autoname or "").startswith("naming_series")
	results = []
	for l in sorted(loans.values(), key=lambda l: l.disbursed_on):
		if l.skip_reason or (only and l.number not in only):
			continue
		if existing := frappe.db.get_value("Loan", {"numero_prestamo": l.number, "docstatus": ("<", 2)}):
			results.append(dict(number=l.number, loan=existing, status="exists"))
			continue

		frappe.db.savepoint("legacy_loan")
		try:
			first_due, early = first_due_date(l)
			loan = frappe.get_doc(
				doctype="Loan",
				applicant_type="Customer",
				applicant=l.customer,
				company=FAM,
				loan_product=PRODUCT,
				posting_date=l.disbursed_on,
				loan_amount=l.amount,
				rate_of_interest=l.rate,
				repayment_method="Repay Over Number of Periods",
				repayment_periods=l.periods,
				repayment_start_date=first_due,
				numero_prestamo=l.number,
			)
			if set_series:
				loan.naming_series = "ACC-LOAN-.YYYY.-"
			loan.insert()
			loan.submit()

			dis = make_loan_disbursement(loan.name, posting_date=l.disbursed_on, disbursement_date=l.disbursed_on)
			dis.set("loan_disbursement_charges", [])  # the legacy fee is already booked in 40003002
			dis.insert()
			dis.submit()

			for paid_on, amount, source in l.payments:
				process_loan_interest_accrual_for_loans(posting_date=add_days(paid_on, -1), loan=loan.name)
				process_daily_loan_demands(posting_date=paid_on, loan=loan.name)
				rep = frappe.get_doc(
					doctype="Loan Repayment",
					against_loan=loan.name,
					loan_disbursement=dis.name,
					repayment_type="Normal Repayment",
					company=FAM,
					posting_date=paid_on,
					value_date=paid_on,
					amount_paid=amount,
					reference_number=source,
					reference_date=paid_on,
				)
				rep.insert()
				rep.submit()

			loan.reload()
			principal = round(loan.disbursed_amount - loan.total_principal_paid, 2)
			results.append(
				dict(
					number=l.number, loan=loan.name, status="created", first_due=first_due,
					early_payments=early, payments=len(l.payments), lending_principal=principal,
					legacy_principal=l.balance_at_cutover,
					difference=round(principal - l.balance_at_cutover, 2),
				)
			)
		except Exception as e:
			frappe.db.rollback(save_point="legacy_loan")
			frappe.clear_messages()
			results.append(dict(number=l.number, status="error", error=str(e)[:300]))

	write_csv(os.path.join(out, "build.csv"), results)
	created = [r for r in results if r["status"] == "created"]
	print(
		f"build: {len(created)} created, {sum(1 for r in results if r['status'] == 'exists')} already existed, "
		f"{sum(1 for r in results if r['status'] == 'error')} errors; "
		f"Lending principal {sum(r['lending_principal'] for r in created):,.2f} vs legacy "
		f"{sum(r['legacy_principal'] for r in created):,.2f}"
	)


def reclass_entry(loans, cutover, submit):
	"""Journal Entry moving the rebuilt loans out of 11301001 into Lending's per-member account."""
	if existing := frappe.db.get_value(
		"Journal Entry",
		{"company": FAM, "docstatus": ("<", 2), "user_remark": ("like", "Reclasificacion de % prestamos personales%")},
	):
		raise SystemExit(f"Reclassification entry {existing} already exists; cancel or delete it first")
	numbers =[l.number for l in loans.values() if not l.skip_reason]
	built = frappe.get_all(
		"Loan",
		filters={"numero_prestamo": ("in", numbers), "docstatus": 1, "company": FAM},
		fields=["name", "numero_prestamo", "applicant_type", "applicant", "disbursed_amount", "total_principal_paid"],
	)
	je = frappe.new_doc("Journal Entry")
	je.company = FAM
	je.posting_date = cutover
	je.user_remark = (
		f"Reclasificacion de {len(built)} prestamos personales reconstruidos en Lending al {cutover}: "
		f"capital pendiente a {LENDING_ACCOUNT}, intereses {cutover.year} cobrados a {INTEREST_ACCOUNT}."
	)
	principal_total = interest_total = 0
	for loan in sorted(built, key=lambda x: x.numero_prestamo):
		principal = round(loan.disbursed_amount - loan.total_principal_paid, 2)
		interest = frappe.db.sql(
			"""select coalesce(sum(total_interest_paid), 0) from `tabLoan Repayment`
			where against_loan=%s and docstatus=1 and value_date between %s and %s""",
			(loan.name, date(cutover.year, 1, 1), cutover),
		)[0][0]
		principal_total += principal
		interest_total += interest
		je.append(
			"accounts",
			dict(
				account=LENDING_ACCOUNT, party_type=loan.applicant_type, party=loan.applicant,
				debit_in_account_currency=principal,
				user_remark=f"{loan.numero_prestamo} {loan.name}",
			),
		)
	je.append("accounts", dict(account=INTEREST_ACCOUNT, credit_in_account_currency=round(interest_total, 2)))
	je.append(
		"accounts",
		dict(account=LOAN_ACCOUNT, credit_in_account_currency=round(principal_total - interest_total, 2)),
	)
	je.insert()
	if submit:
		je.submit()
	print(
		f"reclass {je.name} ({'submitted' if submit else 'draft'}): {len(built)} loans, "
		f"Dr {LENDING_ACCOUNT} {principal_total:,.2f} / Cr {INTEREST_ACCOUNT} {interest_total:,.2f} / "
		f"Cr {LOAN_ACCOUNT} {principal_total - interest_total:,.2f}"
	)


def write_csv(path, rows):
	fields = list(dict.fromkeys(k for r in rows for k in r))
	with open(path, "w", newline="") as f:
		w = csv.DictWriter(f, fieldnames=fields)
		w.writeheader()
		w.writerows(rows)


def main():
	parser = argparse.ArgumentParser()
	parser.add_argument("action", nargs="?", default="report", choices=["report", "build", "reclass"])
	parser.add_argument("--site", default=SITE)
	parser.add_argument("--cutover", default="2026-06-30")
	parser.add_argument("--out", default=".")
	parser.add_argument("--only", nargs="*", help="build only these loan numbers, e.g. 048/25")
	parser.add_argument("--commit", action="store_true", help="build/reclass: save (default rolls back)")
	parser.add_argument("--submit", action="store_true", help="reclass: submit the Journal Entry")
	args = parser.parse_args()
	cutover = date.fromisoformat(args.cutover)

	frappe.init(args.site, sites_path=".")
	frappe.connect()
	frappe.set_user("Administrator")
	loans, events, unmatched, rows = build(cutover)

	os.makedirs(args.out, exist_ok=True)
	fields = [
		"loan", "number", "customer", "customer_name", "match", "disbursed_on", "amount", "fee",
		"installment", "rate", "periods", "n_payments", "paid_total", "refunds", "refinances",
		"refinanced_by", "closed_on", "closed_by_cutover", "balance_at_cutover", "overpaid",
		"interest_by_year", "skip_reason", "voucher", "remark",
	]
	with open(os.path.join(args.out, "loans.csv"), "w", newline="") as f:
		w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
		w.writeheader()
		for l in sorted(loans.values(), key=lambda l: l.disbursed_on):
			w.writerow({k: (json.dumps(v, default=str) if isinstance(v, (list, dict)) else v) for k, v in l.items()})
	with open(os.path.join(args.out, "events.csv"), "w", newline="") as f:
		w = csv.writer(f)
		w.writerow(["loan", "date", "kind", "amount", "source", "remark"])
		w.writerows(sorted(events, key=lambda e: (e[0] or "", e[1])))
	with open(os.path.join(args.out, "unmatched.csv"), "w", newline="") as f:
		w = csv.writer(f)
		w.writerow(["date", "voucher", "amount", "kind", "remark"])
		w.writerows(unmatched)

	ledger = round(sum(r.debit - r.credit for r in rows if r.posting_date <= cutover), 2)
	open_loans = [l for l in loans.values() if l.balance_at_cutover > 0]
	print(f"loans found: {len(loans)}  (member matched: {sum(1 for l in loans.values() if l.customer)}; "
		f"by {dict(Counter(l.match for l in loans.values()))})")
	print(f"terms inferred: {sum(1 for l in loans.values() if l.periods)}; rates {dict(Counter(l.rate for l in loans.values()))}")
	print(f"events: {dict(Counter(e[2] for e in events))}")
	print(f"unmatched 11301001/invoice rows: {len(unmatched)} totalling {sum(u[2] for u in unmatched):,.2f}")
	print(f"open at {cutover}: {len(open_loans)} loans, principal {sum(l.balance_at_cutover for l in open_loans):,.2f}")
	print(f"11301001 balance at {cutover}: {ledger:,.2f}")
	year_interest = sum(l.interest_by_year.get(cutover.year, 0) for l in loans.values())
	print(f"rebuilt {cutover.year} interest (still inside 11301001): {year_interest:,.2f}")
	rebuildable = [l for l in loans.values() if not l.skip_reason]
	print(
		f"rebuildable: {len(rebuildable)} loans, legacy principal {sum(l.balance_at_cutover for l in rebuildable):,.2f}; "
		f"open but skipped: {dict(Counter(l.skip_reason.split(' (')[0].split(':')[0] for l in open_loans if l.skip_reason))}"
	)

	if args.action == "report":
		return
	try:
		if args.action == "build":
			build_loans(loans, args.only, args.out)
		else:
			reclass_entry(loans, cutover, args.submit)
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
