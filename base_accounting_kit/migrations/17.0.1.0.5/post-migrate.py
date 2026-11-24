"""Fix the presentation of the seeded Profit and Loss / Balance Sheet.

The section records are loaded with noupdate="1", so the corrected values in
data/account_financial_report_data.xml only reach new databases; this script
applies them to existing ones. Signs only change how a section's balance is
displayed (balance * sign), never what is summed:

* income sections show positive, cost of revenue / expenses negative;
* liabilities, equity and retained profit show positive on the balance sheet,
  under labels that no longer call equity "Liability".
"""
from odoo import SUPERUSER_ID, api

SECTIONS = {
    'account_financial_report_other_income0': {'sign': '-1'},
    'financial_report_gross_profit': {'sign': '-1'},
    'financial_report_cost_of_revenue': {'sign': '-1'},
    'account_financial_report_operating_income0': {'sign': '-1'},
    'account_financial_report_liabilitysum0': {'sign': '-1', 'name': 'Liabilities and Equity'},
    'account_financial_report_liability0': {'sign': '-1', 'name': 'Liabilities and Capital'},
    'account_financial_report_profitloss_toreport0': {'sign': '-1', 'name': 'Retained Earnings and Current Profit'},
}


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    for xmlid, vals in SECTIONS.items():
        section = env.ref(f'base_accounting_kit.{xmlid}', raise_if_not_found=False)
        if section:
            section.write(vals)
