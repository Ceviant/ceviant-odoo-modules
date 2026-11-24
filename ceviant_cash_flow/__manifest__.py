{
    'name': 'Statement of Cash Flows',
    'version': '17.0.1.0.0',
    'category': 'Accounting/Accounting',
    'summary': 'IAS 7 statement of cash flows (indirect method) for Odoo Community',
    'description': """
Statement of Cash Flows (indirect method)
=========================================
Builds the statement from posted journal items, classifying every account by
its Odoo account type:

* Operating: profit for the period, depreciation added back, and working
  capital movements (receivables, other current assets, prepayments,
  payables and other current liabilities).
* Investing: movement in fixed and other non-current assets, net of the
  depreciation charged.
* Financing: movement in non-current liabilities and equity.

The net cash flow is reconciled to the actual movement in Bank and Cash
accounts; any difference is shown on the statement instead of being hidden.
Available as an on-screen report, PDF and XLSX from
Accounting > Reporting > Statement of Cash Flows.
""",
    'author': 'Ceviant',
    'website': 'https://www.ceviant.co',
    'license': 'LGPL-3',
    'depends': ['account', 'report_xlsx'],
    'data': [
        'security/ir.model.access.csv',
        'report/cash_flow_report.xml',
        'wizard/cash_flow_wizard_views.xml',
    ],
    'installable': True,
    'application': False,
}
