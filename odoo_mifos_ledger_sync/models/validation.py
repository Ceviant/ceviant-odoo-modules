import jsonschema
from jsonschema import validate
import odoorpc
import logging
from odoo.http import request

_logger = logging.getLogger(__name__)

journal_entry_schema = {
    "type": "object",
    "properties": {
        "branchId": {"type": "string"},
        "transactionDate": {"type": "string"},
        "timestamp": {"type": "string"},
        "transactionReference": {"type": "string"},
        "comments": {"type": "string"},
        "currencyCode": {"type": "string"},
        "dateFormat": {"type": "string"},
        "credits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "glAccountId": {"type": "number"},
                    "amount": {"type": "number"}
                },
                "required": ["glAccountId", "amount"]
            }
        },
        "debits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "glAccountId": {"type": "number"},
                    "amount": {"type": "number"}
                },
                "required": ["glAccountId", "amount"]
            }
        }
    },
    "required": ["transactionDate", "transactionReference", "currencyCode", "credits",
                 "debits"]
}

account_entry_schema = {
    "type": "object",
    "properties": {
        "account_id": {"type": "string"},
        "account_name": {"type": "string"},
        "account_type": {"type": "string"},
        "currency": {"type": "string"},
        "status": {"type": "string"},
        "account_code": {"type": "string"}
    },
    "required": ["account_id", "account_code", "account_name", "account_type"]
}


def validate_journal_entry(payload):
    """Validate journal entry payload against the schema"""
    try:
        validate(instance=payload, schema=journal_entry_schema)
        return True, None
    except jsonschema.exceptions.ValidationError as err:
        return False, str(err)


def validate_account_entry(payload):
    """Validate account entry payload against the schema"""
    try:
        validate(instance=payload, schema=account_entry_schema)
        return True, None
    except jsonschema.exceptions.ValidationError as err:
        return False, str(err)


def validate_account_ids(env, account_ids):
    """Validate and map account IDs to Odoo account IDs.
    
    Queries custom_account_entry table for accounts with codes '680' or '425',
    then uses the account_ids from the results for validation.
    Returns a mapping of {original_id: odoo_account_id} for accounts that could be found.
    """
    if not account_ids:
        _logger.warning("No account IDs provided for validation")
        return {}
    
    try:
        account_id_list = [int(id) for id in account_ids if id is not None]
        _logger.info(f"Validating account IDs: {account_id_list}")
    except (ValueError, TypeError) as e:
        _logger.error(f"Error converting account IDs to integers: {e}")
        return {}
    
    id_mapping = {}
    
    # Query custom_account_entry for specific account codes
    target_codes = ('680', '425')
    try:
        env.cr.execute("""
            SELECT DISTINCT account_id, account_code FROM public.custom_account_entry 
            WHERE account_code IN %s
        """, (target_codes,))
        custom_accounts = env.cr.fetchall()
        _logger.info(f"Found {len(custom_accounts)} custom account entries with codes {target_codes}")
        
        # Build a mapping of custom account_ids to their codes
        custom_account_map = {str(row[0]): row[1] for row in custom_accounts}
        _logger.debug(f"Custom account mapping: {custom_account_map}")
    except Exception as e:
        _logger.error(f"Error querying custom_account_entry: {e}")
        custom_account_map = {}
    
    # Validate provided account IDs against custom accounts
    for acc_id in account_id_list:
        acc_id_str = str(acc_id)
        
        # First, try to find accounts directly in account.account
        try:
            existing = env['account.account'].sudo().search([('id', '=', acc_id)], limit=1)
            if existing:
                id_mapping[acc_id] = acc_id
                _logger.debug(f"Found native Odoo account with ID {acc_id}")
                continue
        except Exception as e:
            _logger.debug(f"Could not search for account ID {acc_id}: {str(e)}")
        
        # Check if account_id exists in custom_account_entry with target codes
        if acc_id_str in custom_account_map:
            account_code = custom_account_map[acc_id_str]
            _logger.info(f"Found custom account entry for account_id {acc_id} with code {account_code}")
            
            # Find the corresponding Odoo account by code
            try:
                odoo_account = env['account.account'].sudo().search([
                    ('code', '=', account_code)
                ], limit=1)
                if odoo_account:
                    id_mapping[acc_id] = odoo_account.id
                    _logger.info(f"Mapped custom account {acc_id} (code: {account_code}) to Odoo account {odoo_account.id}")
                else:
                    _logger.warning(f"Custom account {acc_id} exists with code {account_code}, but no Odoo account found")
            except Exception as e:
                _logger.error(f"Error searching for Odoo account with code {account_code}: {str(e)}")
        else:
            _logger.debug(f"Account ID {acc_id} not found in custom_account_entry with target codes {target_codes}")
    
    unmapped = [aid for aid in account_id_list if aid not in id_mapping]
    if unmapped:
        _logger.error(f"Account validation failed for IDs: {unmapped}")
    
    _logger.info(f"Account validation complete. Mapped {len(id_mapping)} of {len(account_id_list)} accounts")
    return id_mapping


def get_currency_id(env, currency_code):
    """Get the currency ID from the currency code."""
    try:
        # Get all currencies and find the one with matching code
        currencies = env['res.currency'].sudo().search([])
        for currency in currencies:
            if currency.code == currency_code:
                _logger.info(f"Found currency {currency_code} with ID {currency.id}")
                return currency.id
        _logger.warning(f"Currency {currency_code} not found")
        return None
    except Exception as e:
        _logger.error(f"Error looking up currency {currency_code}: {e}")
        return None


def get_default_currency():
    """Get Odoo's default/base currency from the company."""
    env = request.env
    company = env['res.company'].sudo().search([], limit=1)
    if company and company.currency_id:
        return company.currency_id.code
    # Fallback to NGN if no company currency found
    return 'NGN'


def get_available_currencies():
    """Get list of all available currencies in the system."""
    env = request.env
    currencies = env['res.currency'].sudo().search_read([], fields=['id', 'code', 'symbol'])
    return [{'id': curr['id'], 'code': curr['code'], 'symbol': curr['symbol']} for curr in currencies]
