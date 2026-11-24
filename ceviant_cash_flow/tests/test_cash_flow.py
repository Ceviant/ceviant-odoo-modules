from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged('post_install', '-at_install')
class TestCashFlowStatement(AccountTestInvoicingCommon):
    """Known scenario, worked by hand:

    2025: capital 1,000 and cash revenue 300 (opening cash for 2026 = 1,300).
    2026: cash revenue 500, credit revenue 100 (receivable), cash expense 200,
          depreciation 50, asset purchase 400, loan drawdown 250.

    2026 indirect method: profit 350 (500 + 100 - 200 - 50), depreciation +50,
    receivables -100 => operating 300; investing -400; financing +250;
    net 150 = closing cash 1,450 - opening 1,300.
    """

    @classmethod
    def setUpClass(cls, chart_template_ref=None):
        super().setUpClass(chart_template_ref=chart_template_ref)
        cls.company = cls.company_data['company']
        cls.journal = cls.company_data['default_journal_misc']

        def account(code, account_type, reconcile=False):
            return cls.env['account.account'].create({
                'code': code, 'name': f'CF test {code}', 'account_type': account_type,
                'company_id': cls.company.id, 'reconcile': reconcile,
            })

        cls.bank = account('CF1000', 'asset_cash')
        cls.receivable = account('CF1100', 'asset_receivable', reconcile=True)
        cls.equipment = account('CF1500', 'asset_fixed')
        cls.accumulated = account('CF1510', 'asset_fixed')
        cls.loan = account('CF2500', 'liability_non_current')
        cls.capital = account('CF3000', 'equity')
        cls.revenue = account('CF4000', 'income')
        cls.expense = account('CF6000', 'expense')
        cls.depreciation = account('CF6800', 'expense_depreciation')

        for date, debit, credit, amount in [
            ('2025-06-01', cls.bank, cls.capital, 1000),
            ('2025-09-01', cls.bank, cls.revenue, 300),
            ('2026-03-01', cls.bank, cls.revenue, 500),
            ('2026-03-15', cls.receivable, cls.revenue, 100),
            ('2026-04-01', cls.expense, cls.bank, 200),
            ('2026-04-30', cls.depreciation, cls.accumulated, 50),
            ('2026-05-01', cls.equipment, cls.bank, 400),
            ('2026-06-01', cls.bank, cls.loan, 250),
        ]:
            move = cls.env['account.move'].create({
                'journal_id': cls.journal.id, 'date': date,
                'line_ids': [(0, 0, {'account_id': debit.id, 'debit': amount}),
                             (0, 0, {'account_id': credit.id, 'credit': amount})],
            })
            move._post(soft=False)

        # A draft entry must be ignored unless draft entries are requested.
        cls.env['account.move'].create({
            'journal_id': cls.journal.id, 'date': '2026-07-01',
            'line_ids': [(0, 0, {'account_id': cls.bank.id, 'debit': 999}),
                         (0, 0, {'account_id': cls.revenue.id, 'credit': 999})],
        })

    def _statement(self, **kwargs):
        return self.env['ceviant.cash.flow.statement'].compute(
            self.company, kwargs.get('date_from', '2026-01-01'), kwargs.get('date_to', '2026-12-31'),
            include_draft=kwargs.get('include_draft', False))

    def _lines(self, statement):
        return {line['key']: line['amount'] for section in statement['sections'] for line in section['lines']}

    def test_indirect_method(self):
        st = self._statement()
        lines = self._lines(st)
        totals = {section['key']: section['total'] for section in st['sections']}

        self.assertAlmostEqual(lines['profit'], 350)
        self.assertAlmostEqual(lines['depreciation'], 50)
        self.assertAlmostEqual(lines['receivables'], -100)
        self.assertAlmostEqual(lines['capex'], -400)
        self.assertAlmostEqual(lines['borrowings'], 250)
        self.assertAlmostEqual(lines['equity'], 0)
        self.assertAlmostEqual(totals['operating'], 300)
        self.assertAlmostEqual(totals['investing'], -400)
        self.assertAlmostEqual(totals['financing'], 250)
        self.assertAlmostEqual(st['net_change'], 150)
        self.assertAlmostEqual(st['opening_cash'], 1300)
        self.assertAlmostEqual(st['closing_cash'], 1450)
        self.assertAlmostEqual(st['difference'], 0)

    def test_prior_year_and_financing(self):
        st = self._statement(date_from='2025-01-01', date_to='2025-12-31')
        totals = {section['key']: section['total'] for section in st['sections']}
        self.assertAlmostEqual(totals['operating'], 300)
        self.assertAlmostEqual(totals['financing'], 1000)
        self.assertAlmostEqual(st['opening_cash'], 0)
        self.assertAlmostEqual(st['closing_cash'], 1300)
        self.assertAlmostEqual(st['difference'], 0)

    def test_draft_entries(self):
        st = self._statement(include_draft=True)
        self.assertAlmostEqual(self._lines(st)['profit'], 350 + 999)
        self.assertAlmostEqual(st['closing_cash'], 1450 + 999)
        self.assertAlmostEqual(st['difference'], 0)

    def test_wizard_renders(self):
        wizard = self.env['ceviant.cash.flow.wizard'].create({
            'company_id': self.company.id, 'date_from': '2026-01-01', 'date_to': '2026-12-31',
            'show_accounts': True})
        html, _fmt = self.env['ir.actions.report']._render_qweb_html(
            'ceviant_cash_flow.report_cash_flow', wizard.ids)
        self.assertIn(b'Net cash from / (used in) operating activities', html)
        self.assertNotIn(b'Unreconciled difference', html)
        xlsx, fmt = self.env['ir.actions.report']._render(
            'ceviant_cash_flow.report_cash_flow_xlsx', wizard.ids)
        self.assertEqual(fmt, 'xlsx')
        self.assertTrue(xlsx)
