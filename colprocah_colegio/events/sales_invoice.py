import frappe

def set_original_posting_date(doc, method):
    if not doc.custom_original_posting_date:
        doc.custom_original_posting_date = doc.posting_date
