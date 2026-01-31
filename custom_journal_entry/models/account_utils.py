import logging
from odoo.http import request
from .validation import validate_account_entry, get_currency_id
from .rabbitmq_publisher import generate_batch_reference

_logger = logging.getLogger(__name__)

# Global cache for valid account types (performance optimization)
_valid_account_types_cache = None

# Account type mapping for common user inputs to Odoo account types
ACCOUNT_TYPE_MAPPING = {
    'ASSET': 'asset_fixed',
    'CURRENT_ASSET': 'asset_current',
    'FIXED_ASSET': 'asset_fixed',
    'LIABILITY': 'liability_current',
    'CURRENT_LIABILITY': 'liability_current',
    'NON_CURRENT_LIABILITY': 'liability_non_current',
    'PAYABLE': 'liability_payable',
    'RECEIVABLE': 'asset_receivable',
    'EQUITY': 'equity',
    'INCOME': 'income',
    'EXPENSE': 'expense',
    'DEPRECIATION': 'expense_depreciation',
    'PREPAID': 'asset_prepaid',
    'OTHER_INCOME': 'income_other',
    'DIRECT_COST': 'expense_direct_cost',
}

def map_account_type(account_type):
    """Map common account type names to Odoo's specific account type codes."""
    if not account_type:
        return None
    
    mapped_type = ACCOUNT_TYPE_MAPPING.get(account_type.upper())
    if mapped_type:
        _logger.info(f"Mapped account type '{account_type}' to '{mapped_type}'")
        return mapped_type
    
    return account_type


def _get_valid_account_types(env):
    """Retrieve and cache valid account types from Odoo (performance optimization)."""
    global _valid_account_types_cache
    if _valid_account_types_cache is None:
        accounts = env['account.account'].sudo().search_read([], fields=['account_type'])
        _valid_account_types_cache = frozenset(
            record['account_type'] for record in accounts if record['account_type']
        )
        _logger.debug(f"Cached {len(_valid_account_types_cache)} account types")
    return _valid_account_types_cache

def create_account(payload):
    """Create a new account in Odoo with validation and error handling."""
    env = request.env
    Account = env['account.account'].sudo()  # Use sudo to bypass ACL restrictions
    CustomAccountEntry = env['custom.account.entry'].sudo()

    # Validate payload early (fail-fast pattern)
    is_valid, validation_error = validate_account_entry(payload)
    if not is_valid:
        _logger.error(f"Payload validation failed: {validation_error}")
        return None, validation_error

    # Map and validate account type
    account_type_name = map_account_type(payload.get('account_type'))
    if not account_type_name:
        return None, "Account type is required"

    valid_account_types = _get_valid_account_types(env)
    if account_type_name not in valid_account_types:
        return None, f"Invalid account type '{account_type_name}' provided."

    # Validate currency
    currency_id = get_currency_id(payload['currency'])
    if not currency_id:
        return None, "Invalid currency"

    # Check for duplicate account code
    code = payload.get('account_code')
    if Account.search([('code', '=', code)], limit=1):
        return None, f"Account with code '{code}' already exists."

    try:
        new_account = Account.create({
            'code': payload['account_code'],
            'name': payload['account_name'],
            'account_type': account_type_name,
            'currency_id': currency_id,
            'reconcile': payload.get('account_status', '').lower() == 'active',
        })

        new_custom_account = CustomAccountEntry.create({
            'account_id': payload['account_id'],
            'account_name': payload['account_name'],
            'account_type': account_type_name,
            'currency_id': currency_id,
            'account_code': payload['account_code'],
        })

        batch_ref = generate_batch_reference()
        _logger.info(f"Account '{payload['account_name']}' created with batch ref: {batch_ref}")
        return batch_ref, None

    except Exception as e:
        _logger.error(f"Account creation error: {str(e)}")
        return None, f"Error creating account: {str(e)}"

def get_account_data(env, accounts):
    """Convert account records to dictionary format for API response (optimized with list comprehension)."""
    account_data = [
        {
            'id': account.id,
            'code': account.code,
            'name': account.name,
            'account_type': account.account_type or '',
            'currency_id': account.currency_id.name if account.currency_id else '',
            'reconcile': account.reconcile
        }
        for account in accounts
    ]
    _logger.debug(f"Prepared {len(account_data)} account records")
    return account_data


def get_account_records(env):
    """Retrieve all account records from Odoo."""
    return env['account.account'].sudo().search([])


def clear_account_type_cache():
    """Clear the cached account types (useful for testing or after Odoo updates)."""
    global _valid_account_types_cache
    _valid_account_types_cache = None
    _logger.debug("Account type cache cleared")