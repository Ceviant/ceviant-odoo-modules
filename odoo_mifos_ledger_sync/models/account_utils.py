"""Mirror Fineract GL accounts into the Odoo chart of accounts."""
import logging

from .journal_utils import LedgerSyncError, resolve_company
from .validation import validate_account_entry, get_currency_id

_logger = logging.getLogger(__name__)

# Fineract GLAccountType names (and finer-grained aliases) -> Odoo account types.
# Fineract only distinguishes the five classes, so a plain ASSET lands in
# current assets; finance can reclassify fixed / cash accounts in Odoo, and
# later syncs never overwrite the type of an existing account.
ACCOUNT_TYPE_MAPPING = {
    'ASSET': 'asset_current',
    'LIABILITY': 'liability_current',
    'EQUITY': 'equity',
    'INCOME': 'income',
    'EXPENSE': 'expense',
    'CURRENT_ASSET': 'asset_current',
    'FIXED_ASSET': 'asset_fixed',
    'NON_CURRENT_ASSET': 'asset_non_current',
    'CASH': 'asset_cash',
    'RECEIVABLE': 'asset_receivable',
    'PREPAID': 'asset_prepayments',
    'CURRENT_LIABILITY': 'liability_current',
    'NON_CURRENT_LIABILITY': 'liability_non_current',
    'PAYABLE': 'liability_payable',
    'OTHER_INCOME': 'income_other',
    'DEPRECIATION': 'expense_depreciation',
    'DIRECT_COST': 'expense_direct_cost',
}

# Odoo requires these types to be reconcilable.
_RECONCILE_TYPES = {'asset_receivable', 'liability_payable'}


def map_account_type(env, account_type):
    """Return the Odoo account type for a Fineract/alias type, or None if invalid."""
    if not account_type:
        return None
    mapped = ACCOUNT_TYPE_MAPPING.get(account_type.upper(), account_type)
    valid = {key for key, _label in env['account.account']._fields['account_type'].selection}
    return mapped if mapped in valid else None


def create_account(env, payload):
    """Create (or find) the Odoo account mirroring a Fineract GL account.

    Idempotent on account_code within the sync company.
    Returns (account, created).
    """
    is_valid, error = validate_account_entry(payload)
    if not is_valid:
        raise LedgerSyncError(f"Invalid payload: {error}")

    account_type = map_account_type(env, payload.get('account_type'))
    if not account_type:
        raise LedgerSyncError(f"Invalid account type {payload.get('account_type')!r}")

    company = resolve_company(env)
    Account = env['account.account'].sudo().with_company(company)
    code = str(payload['account_code'])

    # Fineract amounts are in the transaction currency, so only lock the
    # account to a currency when one is sent and it is foreign to the company.
    currency_id = False
    if payload.get('currency'):
        currency_id = get_currency_id(env, payload['currency'])
        if not currency_id:
            raise LedgerSyncError(f"Unknown or inactive currency {payload['currency']!r}")
        if currency_id == company.currency_id.id:
            currency_id = False

    account = Account.search([('company_id', '=', company.id), ('code', '=', code)], limit=1)
    created = not account
    if created:
        account = Account.create({
            'code': code,
            'name': payload['account_name'],
            'account_type': account_type,
            'currency_id': currency_id,
            'reconcile': account_type in _RECONCILE_TYPES,
            'company_id': company.id,
        })
        _logger.info("Ledger sync: created account %s %s (%s)", code, payload['account_name'], account_type)

    status = (payload.get('account_status') or payload.get('status') or '').lower()
    if status == 'inactive' and not account.deprecated:
        account.deprecated = True

    CustomAccountEntry = env['custom.account.entry'].sudo()
    if not CustomAccountEntry.search([('account_code', '=', code)], limit=1):
        CustomAccountEntry.create({
            'account_id': payload['account_id'],
            'account_name': payload['account_name'],
            'account_type': account_type,
            'currency_id': currency_id or company.currency_id.id,
            'account_code': code,
        })
    return account, created


def get_account_data(accounts):
    """Serialise account records for the API response."""
    return [
        {
            'id': account.id,
            'code': account.code,
            'name': account.name,
            'account_type': account.account_type or '',
            'currency_id': account.currency_id.name if account.currency_id else '',
            'reconcile': account.reconcile,
        }
        for account in accounts
    ]


def get_account_records(env):
    """All accounts of the ledger sync company."""
    company = resolve_company(env)
    return env['account.account'].sudo().search([('company_id', '=', company.id)])
