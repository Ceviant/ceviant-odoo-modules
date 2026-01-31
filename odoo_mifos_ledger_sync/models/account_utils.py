import logging
import json
from odoo.http import request, Response
from .validation import validate_account_entry, get_currency_id
from .rabbitmq_publisher import generate_batch_reference

_logger = logging.getLogger(__name__)

# Account type mapping for Fineract and common user inputs to Odoo account types
# Supports both Fineract's 5 main types and Odoo's more specific types
ACCOUNT_TYPE_MAPPING = {
    # Fineract main types
    'ASSET': 'asset_fixed',
    'LIABILITY': 'liability_current',
    'EQUITY': 'equity',
    'INCOME': 'income',
    'EXPENSE': 'expense',
    
    # Fineract asset subtypes
    'CURRENT_ASSET': 'asset_current',
    'FIXED_ASSET': 'asset_fixed',
    'RECEIVABLE': 'asset_receivable',
    'PREPAID': 'asset_prepaid',
    
    # Fineract liability subtypes
    'CURRENT_LIABILITY': 'liability_current',
    'NON_CURRENT_LIABILITY': 'liability_non_current',
    'PAYABLE': 'liability_payable',
    
    # Fineract income subtypes
    'OTHER_INCOME': 'income_other',
    
    # Fineract expense subtypes
    'DEPRECIATION': 'expense_depreciation',
    'DIRECT_COST': 'expense_direct_cost',
}

# Cache for valid Odoo account types (populated on first use)
_valid_account_types_cache = None

def map_account_type(account_type):
    """
    Map common account type names to Odoo's specific account type codes.
    If the account type is already in Odoo format, return it as-is.
    Returns None if account_type is empty/None.
    """
    if not account_type:
        return None
    
    account_type_upper = account_type.upper()
    mapped_type = ACCOUNT_TYPE_MAPPING.get(account_type_upper)
    
    if mapped_type:
        _logger.debug(f"Mapped account type '{account_type}' to '{mapped_type}'")
        return mapped_type
    
    # Return as-is if not in mapping (assume it's already in Odoo format)
    return account_type

def _get_valid_account_types(env):
    """
    Get valid account types from Odoo with caching.
    Cache is per-environment to handle multi-tenancy.
    """
    global _valid_account_types_cache
    
    if _valid_account_types_cache is None:
        try:
            accounts = env['account.account'].sudo().search_read([], fields=['account_type'])
            _valid_account_types_cache = frozenset(
                record['account_type'] for record in accounts if record['account_type']
            )
            _logger.debug(f"Cached {len(_valid_account_types_cache)} valid account types")
        except Exception as e:
            _logger.error(f"Failed to retrieve account types from Odoo: {e}")
            return frozenset()
    
    return _valid_account_types_cache

def create_account(payload):
    """Create a new account in Odoo with validation and error handling."""
    env = request.env
    Account = env['account.account'].sudo()  # Use sudo to bypass ACL restrictions
    CustomAccountEntry = env['custom.account.entry'].sudo()

    # Validate payload structure
    is_valid, validation_error = validate_account_entry(payload)
    if not is_valid:
        _logger.error(f"Payload validation failed: {validation_error}")
        return None, validation_error

    # Map and validate account type
    account_type_name = map_account_type(payload.get('account_type'))
    if not account_type_name:
        error_message = "Account type is required"
        _logger.error(error_message)
        return None, error_message

    # Validate account type exists in Odoo
    valid_account_types = _get_valid_account_types(env)
    if account_type_name not in valid_account_types:
        error_message = f"Invalid account type '{account_type_name}' provided."
        _logger.error(error_message)
        return None, error_message

    # Validate currency
    currency_id = get_currency_id(payload['currency'])
    if not currency_id:
        error_message = "Invalid currency"
        _logger.error(error_message)
        return None, error_message

    # Check for duplicate account code
    code = payload.get('account_code')
    if Account.search([('code', '=', code)], limit=1):
        error_message = f"Account with code '{code}' already exists."
        _logger.error(error_message)
        return None, error_message

    try:
        # Create main Odoo account
        new_account = Account.create({
            'code': payload['account_code'],
            'name': payload['account_name'],
            'account_type': account_type_name,
            'currency_id': currency_id,
            'reconcile': payload.get('status', '').lower() == 'active',
        })

        # Create custom account entry
        CustomAccountEntry.create({
            'account_id': payload['account_id'],
            'account_name': payload['account_name'],
            'account_type': account_type_name,
            'currency_id': currency_id,
            'account_code': payload['account_code'],
        })

        batch_ref = generate_batch_reference()
        _logger.info(f"Account '{payload['account_name']}' created with ID {new_account.id}. Batch: {batch_ref}")
        return batch_ref, None

    except Exception as e:
        error_msg = str(e)
        _logger.error(f"Failed to create account: {error_msg}")
        return None, f"Error creating account: {error_msg}"

def get_account_data(env, accounts):
    """Convert account records to dictionary format for API response."""
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