import frappe
from frappe import _

import frappe
from frappe import _

def execute(filters=None):
    columns = get_columns()
    data, customer_info = get_data(filters)
    
    # Pass customer info as report summary / message
    report_summary = None
    if customer_info:
        report_summary = customer_info  # we'll use custom_data instead

    return columns, data, None, None, customer_info  # 5th return = custom data

def get_data(filters):
    # --- Fetch customer info ---
    customer_info = {}
    if filters and filters.get("customer"):
        customer_info = frappe.db.get_value(
            "Customer",
            filters["customer"],
            ["customer_name", "codigo_colegiado", "fecha_ingreso"],
            as_dict=True
        ) or {}

    # ... rest of your existing get_data logic ...
    submitted_conditions, submitted_values = build_filters(filters, docstatus=1)
    submitted = frappe.db.sql(f"""
        SELECT
            YEAR(si.custom_original_posting_date)  AS year,
            MONTH(si.custom_original_posting_date) AS month,
            SUM(sii.amount)                        AS total
        FROM `tabSales Invoice` si
        INNER JOIN `tabSales Invoice Item` sii
            ON sii.parent = si.name
        WHERE {submitted_conditions}
        GROUP BY
            YEAR(si.custom_original_posting_date),
            MONTH(si.custom_original_posting_date)
        ORDER BY year, month
    """, submitted_values, as_dict=True)

    mora_conditions, mora_values = build_filters(filters, docstatus=0)
    mora_rows = frappe.db.sql(f"""
        SELECT
            YEAR(si.custom_original_posting_date) AS year,
            SUM(sii.amount)                       AS total
        FROM `tabSales Invoice` si
        INNER JOIN `tabSales Invoice Item` sii
            ON sii.parent = si.name
        WHERE {mora_conditions}
        GROUP BY
            YEAR(si.custom_original_posting_date)
        ORDER BY year
    """, mora_values, as_dict=True)

    mora_by_year = {int(r.year): r.total for r in mora_rows}

    pivot = {}
    for row in submitted:
        y = int(row.year)
        m = int(row.month)
        pivot.setdefault(y, {})[m] = row.total

    data = []
    for year in sorted(pivot.keys()):
        row = {"year": year}
        year_total = 0
        for m in range(1, 13):
            val = pivot[year].get(m, 0)
            row[f"month_{m:02d}"] = val
            year_total += val
        row["total_pagado"] = year_total
        row["mora"] = mora_by_year.get(year, 0)
        data.append(row)

    return data, customer_info

def get_columns():
    months = [
        "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
        "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"
    ]
    columns = [
        {
            "label": _("Year"),
            "fieldname": "year",
            "fieldtype": "Data",
            "width": 80,
            "disable_total": 1
        }
    ]
    for i, month in enumerate(months, start=1):
        columns.append({
            "label": _(month),
            "fieldname": f"month_{i:02d}",
            "fieldtype": "Currency",
            "width": 120
        })
    columns += [
        {
            "label": _("Total Pagado"),
            "fieldname": "total_pagado",
            "fieldtype": "Currency",
            "width": 130
        },
        {
            "label": _("Mora"),
            "fieldname": "mora",
            "fieldtype": "Currency",
            "width": 130
        }
    ]
    return columns

def build_filters(filters, docstatus):
    conditions = [
        f"si.docstatus = {docstatus}",
        "sii.item_code = %(item_code)s",
        "si.custom_original_posting_date IS NOT NULL"
    ]
    values = {"item_code": "CO-I"}

    if filters:
        if filters.get("customer"):
            conditions.append("si.customer = %(customer)s")
            values["customer"] = filters["customer"]

        if filters.get("from_date"):
            conditions.append("si.custom_original_posting_date >= %(from_date)s")
            values["from_date"] = filters["from_date"]

        if filters.get("to_date"):
            conditions.append("si.custom_original_posting_date <= %(to_date)s")
            values["to_date"] = filters["to_date"]

    return " AND ".join(conditions), values

# def get_data(filters):
#     # --- Submitted invoices (monthly pivot) ---
#     submitted_conditions, submitted_values = build_filters(filters, docstatus=1)

#     submitted = frappe.db.sql(f"""
#         SELECT
#             YEAR(si.custom_original_posting_date)  AS year,
#             MONTH(si.custom_original_posting_date) AS month,
#             SUM(sii.amount)                        AS total
#         FROM `tabSales Invoice` si
#         INNER JOIN `tabSales Invoice Item` sii
#             ON sii.parent = si.name
#         WHERE {submitted_conditions}
#         GROUP BY
#             YEAR(si.custom_original_posting_date),
#             MONTH(si.custom_original_posting_date)
#         ORDER BY year, month
#     """, submitted_values, as_dict=True)

#     # --- Draft invoices (mora, grouped by year only) ---
#     mora_conditions, mora_values = build_filters(filters, docstatus=0)

#     mora_rows = frappe.db.sql(f"""
#         SELECT
#             YEAR(si.custom_original_posting_date) AS year,
#             SUM(sii.amount)                       AS total
#         FROM `tabSales Invoice` si
#         INNER JOIN `tabSales Invoice Item` sii
#             ON sii.parent = si.name
#         WHERE {mora_conditions}
#         GROUP BY
#             YEAR(si.custom_original_posting_date)
#         ORDER BY year
#     """, mora_values, as_dict=True)

#     mora_by_year = {int(r.year): r.total for r in mora_rows}

#     # --- Build pivot: { year: { month: total } } ---
#     pivot = {}
#     for row in submitted:
#         y = int(row.year)
#         m = int(row.month)
#         pivot.setdefault(y, {})[m] = row.total

#     # --- Build final rows ---
#     data = []
#     for year in sorted(pivot.keys()):
#         row = {"year": year}
#         year_total = 0
#         for m in range(1, 13):
#             val = pivot[year].get(m, 0)
#             row[f"month_{m:02d}"] = val
#             year_total += val
#         row["total_pagado"] = year_total
#         row["mora"] = mora_by_year.get(year, 0)
#         data.append(row)

#     return data