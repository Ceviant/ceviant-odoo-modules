"""One-off data fixes for the Fineract ledger company, run with ``odoo shell``.

    APPLY=0 odoo shell -d <db> --no-http < odoo_data_fixes.py   # dry run (default)
    APPLY=1 odoo shell -d <db> --no-http < odoo_data_fixes.py   # commit

Run it after upgrading odoo_mifos_ledger_sync, base_accounting_kit,
invoice_multi_approval, dynamic_accounts_report, advance_cash_flow_statements
and installing ceviant_cash_flow. Every step is idempotent.

1. Company currency -> NGN. Every journal item of the company is already
   NGN-based (company_currency_id = NGN); only res_company says USD.
   Skipped if any journal item uses another company currency.
2. Remove the currency lock from the Fineract mirror accounts (all Fineract
   amounts are NGN; the old sync locked them to the company's USD, which
   blocks posting).
3. Reclassify mirror accounts the old sync typed from Fineract's 5 classes
   (every ASSET became Fixed Assets), using Fineract's GL code structure.
4. Fix two misclassified chart accounts: 88166 accumulated depreciation
   (typed as an expense, so depreciation cancelled out inside the P&L) and
   10130 interest receivable (typed as Bank and Cash).
"""
import os
import re

APPLY = os.environ.get('APPLY') == '1'
COMPANY_ID = int(os.environ.get('LEDGER_COMPANY_ID', '1'))

company = env['res.company'].browse(COMPANY_ID)
ngn = env.ref('base.NGN')
Account = env['account.account'].with_context(active_test=False)
print(f"{'APPLYING' if APPLY else 'DRY RUN'} for company {company.id} {company.name}")

# 1. Company currency
# Bulk-inserted Fineract lines have no company currency at all; they are
# rebuilt when Fineract resends them, so only a different *set* currency blocks.
other = env['account.move.line'].search_count([
    ('company_id', '=', company.id), ('company_currency_id', '!=', False),
    ('company_currency_id', '!=', ngn.id)])
if company.currency_id == ngn:
    print('1. company currency already NGN')
elif other:
    print(f'1. SKIPPED: {other} journal items use a non-NGN company currency; resolve manually')
else:
    print(f'1. company currency {company.currency_id.name} -> NGN')
    # The ORM refuses to change the currency of a company with entries.
    env.cr.execute('UPDATE res_company SET currency_id = %s WHERE id = %s', (ngn.id, company.id))
    company.invalidate_recordset(['currency_id'])

# Mirror accounts: account.code = Fineract GL id, custom.account.entry.account_id = Fineract GL code.
entries = env['custom.account.entry'].search([])
gl_code = {e.account_code: e.account_id for e in entries}
mirror = Account.search([('company_id', '=', company.id), ('code', 'in', list(gl_code))])
print(f'   {len(mirror)} Fineract mirror accounts')

# 2. Currency locks
locked = mirror.filtered('currency_id')
print(f'2. clear currency lock on {len(locked)} mirror accounts')
if APPLY and locked:
    locked.write({'currency_id': False})

# 3. Reclassify by Fineract GL code
ASSET_RULES = [
    (r'^10', 'asset_cash'),                 # cash, banks, settlement accounts
    (r'^1[1-58-9]', 'asset_current'),       # investments, receivables, derivatives, held for sale
    (r'^16', 'asset_prepayments'),
    (r'^(17|2[1-5])', 'asset_non_current'),  # deferred tax, intangibles, investment property, subsidiaries
]                                           # 20xxx property, plant and equipment stay asset_fixed


def target_type(account):
    code = gl_code.get(account.code) or ''
    if account.account_type == 'asset_fixed':
        for pattern, new_type in ASSET_RULES:
            if re.match(pattern, code):
                return new_type
    if account.account_type == 'liability_current' and code.startswith('4'):
        return 'liability_non_current'      # Fineract class 4x = non-current liabilities
    return account.account_type


changes = {}
for account in mirror:
    new_type = target_type(account)
    if new_type != account.account_type:
        changes.setdefault((account.account_type, new_type), env['account.account'])
        changes[(account.account_type, new_type)] |= account
for (old, new), accounts in sorted(changes.items()):
    print(f'3. {old} -> {new}: {len(accounts)} accounts ({", ".join(accounts[:5].mapped("name"))}...)')
    if APPLY:
        accounts.write({'account_type': new})
        entries.filtered(lambda e: e.account_code in accounts.mapped('code')).write({'account_type': new})

# 4. Known misclassified chart accounts
for code, expected_old, new in [('88166', 'expense_depreciation', 'asset_fixed'),
                                ('10130', 'asset_cash', 'asset_current')]:
    account = Account.search([('company_id', '=', company.id), ('code', '=', code)])
    if account and account.account_type == expected_old:
        print(f'4. {code} {account.name}: {expected_old} -> {new}')
        if APPLY:
            account.account_type = new
    else:
        print(f'4. {code}: nothing to do ({account.account_type if account else "not found"})')

if APPLY:
    env.cr.commit()
    print('committed')
else:
    env.cr.rollback()
    print('rolled back (dry run); re-run with APPLY=1 to commit')
