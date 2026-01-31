import logging
import json
from odoo.http import request, Response
from .validation import validate_account_entry, get_currency_id
from .rabbitmq_publisher import generate_batch_reference

_logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

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
    """
    Map common account type names to Odoo's specific account type codes.
    If the account type is already in Odoo format, return it as-is.
    """
    if not account_type:
        return None
    
    # Check if it's a common name that needs mapping
    mapped_type = ACCOUNT_TYPE_MAPPING.get(account_type.upper())
    if mapped_type:
        _logger.info(f"Mapped account type '{account_type}' to '{mapped_type}'")
        return mapped_type
    
    # Return as-is if not in mapping (assume it's already in Odoo format)
    return account_type

def create_account(payload):
    env = request.env
    Account = env['account.account']
    CustomAccountEntry = env['custom.account.entry']

    is_valid, validation_error = validate_account_entry(payload)
    if not is_valid:
        _logger.error(f"Payload validation failed: {validation_error}")
        return None, validation_error

    # Map the account type to Odoo format
    account_type_name = map_account_type(payload.get('account_type'))
    if not account_type_name:
        error_message = "Account type is required"
        _logger.error(error_message)
        return None, error_message

    account_types = Account.sudo().search_read([], fields=['account_type'])
    valid_account_types = {record['account_type'] for record in account_types if record['account_type']}
    _logger.info(f"Valid account types retrieved from Odoo: {valid_account_types}")

    if account_type_name not in valid_account_types:
        error_message = f"Invalid account type '{account_type_name}' provided."
        _logger.error(error_message)
        return None, error_message

    currency_id = get_currency_id(payload['currency'])
    if not currency_id:
        error_message = "Invalid currency"
        _logger.error(error_message)
        return None, error_message

    code = payload.get('account_code')
    existing_account = Account.search([('code', '=', code)], limit=1)
    if existing_account:
        error_message = f"Account with code '{code}' already exists."
        _logger.error(error_message)
        return None, error_message

    try:
        new_account = Account.create({
            'code': payload['account_code'],
            'name': payload['account_name'],
            'account_type': account_type_name,
            'currency_id': currency_id,
            'reconcile': payload['status'].lower() == 'active',
        })

        new_custom_account = CustomAccountEntry.create({
            'account_id': payload['account_id'],
            'account_name': payload['account_name'],
            'account_type': account_type_name,
            'currency_id': currency_id,
            'account_code': payload['account_code'],
        })

        _logger.info(f"Account '{payload['account_name']}' created successfully with ID {new_account.id} and custom entry ID {new_custom_account.id}.")
        return generate_batch_reference(), None

    except Exception as e:
        _logger.error(f"Failed to create new account: {str(e)}")
        return None, f"Error creating account: {str(e)}"

def get_account_data(env, accounts):
    account_data = []
    for account in accounts:
        account_data.append({
            'id': account.id,
            'code': account.code,
            'name': account.name,
            'account_type': account.account_type if account.account_type else '',
            'currency_id': account.currency_id.name if account.currency_id else '',
            'reconcile': account.reconcile
        })

    _logger.info(f"Account data prepared: {account_data}")
    return account_data


def get_account_records(env):
    return env['account.account'].search([])