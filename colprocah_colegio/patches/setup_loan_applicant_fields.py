from colprocah_colegio.lending import backfill_applicant_details, setup_loan_fields


def execute():
	setup_loan_fields()
	backfill_applicant_details()
