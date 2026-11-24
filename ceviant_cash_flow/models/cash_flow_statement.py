"""Indirect-method statement of cash flows computed from journal items."""
from collections import defaultdict

from odoo import _, api, models

PROFIT_AND_LOSS_TYPES = (
    'income', 'income_other', 'expense', 'expense_depreciation', 'expense_direct_cost',
)
CASH_TYPES = ('asset_cash',)
DEPRECIATION_TYPES = ('expense_depreciation',)
INVESTING_TYPES = ('asset_fixed', 'asset_non_current')

# (key, label, account types). Each line shows -(movement) of its accounts:
# a decrease in an asset or an increase in a liability/equity releases cash.
WORKING_CAPITAL_LINES = [
    ('receivables', '(Increase) / decrease in trade receivables', ('asset_receivable',)),
    ('current_assets', '(Increase) / decrease in other current assets', ('asset_current',)),
    ('prepayments', '(Increase) / decrease in prepayments', ('asset_prepayments',)),
    ('payables', 'Increase / (decrease) in trade payables', ('liability_payable', 'liability_credit_card')),
    ('current_liabilities', 'Increase / (decrease) in other current liabilities, including customer deposits',
     ('liability_current',)),
]
FINANCING_LINES = [
    ('borrowings', 'Proceeds from / (repayment of) non-current borrowings', ('liability_non_current',)),
    ('equity', 'Capital contributed / (distributed)', ('equity', 'equity_unaffected')),
]


class CashFlowStatement(models.AbstractModel):
    _name = 'ceviant.cash.flow.statement'
    _description = 'Statement of Cash Flows (indirect method)'

    @api.model
    def _movements(self, company, date_from, date_to, states):
        """Sum of journal item balances per account over [date_from, date_to]."""
        domain = [
            ('company_id', '=', company.id),
            ('parent_state', 'in', states),
            ('date', '<=', date_to),
            ('account_id.account_type', '!=', 'off_balance'),
        ]
        if date_from:
            domain.append(('date', '>=', date_from))
        groups = self.env['account.move.line'].sudo()._read_group(domain, ['account_id'], ['balance:sum'])
        return {account: balance for account, balance in groups if not company.currency_id.is_zero(balance)}

    @api.model
    def _cash_balance(self, company, date_to, states, strictly_before=False):
        domain = [
            ('company_id', '=', company.id),
            ('parent_state', 'in', states),
            ('date', '<' if strictly_before else '<=', date_to),
            ('account_id.account_type', 'in', CASH_TYPES),
        ]
        groups = self.env['account.move.line'].sudo()._read_group(domain, [], ['balance:sum'])
        return groups[0][0] or 0.0

    @api.model
    def _line(self, key, label, amount, accounts=None, sign=-1, extra_details=None):
        details = [
            {'label': f'{account.code} {account.name}', 'amount': sign * balance}
            for account, balance in sorted((accounts or {}).items(), key=lambda item: item[0].code or '')
        ]
        details += extra_details or []
        return {'key': key, 'label': label, 'amount': amount, 'details': details}

    @api.model
    def compute(self, company, date_from, date_to, include_draft=False):
        """Return the statement as a dict of sections, totals and the cash check.

        Every line is derived from account movements in the period, so by
        double entry: net cash flow == movement of Bank and Cash accounts,
        unless some account type is not classified (reported as a difference).
        """
        states = ['posted', 'draft'] if include_draft else ['posted']
        movements = self._movements(company, date_from, date_to, states)

        by_type = defaultdict(dict)
        for account, balance in movements.items():
            by_type[account.account_type][account] = balance

        def accounts_of(types):
            merged = {}
            for account_type in types:
                merged.update(by_type.get(account_type, {}))
            return merged

        def total(types):
            return sum(accounts_of(types).values())

        profit = -total(PROFIT_AND_LOSS_TYPES)
        depreciation = total(DEPRECIATION_TYPES)

        operating = [
            self._line('profit', _('Profit / (loss) for the period'), profit,
                       accounts_of(PROFIT_AND_LOSS_TYPES)),
            self._line('depreciation', _('Adjustment: depreciation and amortisation (non-cash)'), depreciation,
                       accounts_of(DEPRECIATION_TYPES), sign=1),
        ]
        operating += [self._line(key, label, -total(types), accounts_of(types))
                      for key, label, types in WORKING_CAPITAL_LINES]

        # Depreciation was added back under operating activities; the same
        # charge reduced the asset accounts, so it is taken out of the
        # investing movement to leave only purchases and disposals.
        investing = [self._line(
            'capex', _('Net (purchase) / disposal of property, plant, equipment and other non-current assets'),
            -total(INVESTING_TYPES) - depreciation, accounts_of(INVESTING_TYPES),
            extra_details=[{'label': _('Less: depreciation charged (non-cash)'), 'amount': -depreciation}]
            if depreciation else [])]

        financing = [self._line(key, label, -total(types), accounts_of(types)) for key, label, types in FINANCING_LINES]

        sections = [
            {'key': 'operating', 'label': _('Cash flows from operating activities'),
             'total_label': _('Net cash from / (used in) operating activities'), 'lines': operating},
            {'key': 'investing', 'label': _('Cash flows from investing activities'),
             'total_label': _('Net cash from / (used in) investing activities'), 'lines': investing},
            {'key': 'financing', 'label': _('Cash flows from financing activities'),
             'total_label': _('Net cash from / (used in) financing activities'), 'lines': financing},
        ]
        for section in sections:
            section['total'] = sum(line['amount'] for line in section['lines'])
        net_change = sum(section['total'] for section in sections)

        opening_cash = self._cash_balance(company, date_from, states, strictly_before=True) if date_from else 0.0
        cash_movement = total(CASH_TYPES)
        closing_cash = opening_cash + cash_movement

        classified = set(PROFIT_AND_LOSS_TYPES) | set(CASH_TYPES) | set(INVESTING_TYPES)
        for _key, _label, types in WORKING_CAPITAL_LINES + FINANCING_LINES:
            classified |= set(types)
        unclassified = {account: balance for account, balance in movements.items()
                        if account.account_type not in classified}

        return {
            'company': company,
            'currency': company.currency_id,
            'date_from': date_from,
            'date_to': date_to,
            'include_draft': include_draft,
            'sections': sections,
            'net_change': net_change,
            'opening_cash': opening_cash,
            'cash_movement': cash_movement,
            'closing_cash': closing_cash,
            'difference': cash_movement - net_change,
            'cash_accounts': self._line('cash', _('Bank and cash movement'), cash_movement,
                                        accounts_of(CASH_TYPES), sign=1)['details'],
            'unclassified': self._line('unclassified', _('Unclassified'), 0.0, unclassified, sign=-1)['details'],
        }
