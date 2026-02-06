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
    
    First checks if the IDs exist directly in account.account.
    If not found, attempts to map them from custom.account.entry using account_code.
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
    
    # First, try to find accounts directly in account.account
    for acc_id in account_id_list:
        try:
            existing = env['account.account'].sudo().search([('id', '=', acc_id)], limit=1)
            if existing:
                id_mapping[acc_id] = acc_id
                _logger.debug(f"Found native Odoo account with ID {acc_id}")
        except Exception as e:
            _logger.debug(f"Could not search for account ID {acc_id}: {str(e)}")
    
    # If some IDs are missing, try to map them from custom.account.entry
    unmapped_ids = [aid for aid in account_id_list if aid not in id_mapping]
    if unmapped_ids:
        _logger.info(f"Attempting to find custom account entries for IDs: {unmapped_ids}")
        
        for custom_id in unmapped_ids:
            try:
                # Use raw SQL to search for custom account entries
                try:
                    env.cr.execute("""
                        SELECT account_code FROM custom_account_entry 
                        WHERE account_id = %s
                        LIMIT 1
                    """, (str(custom_id),))
                    result = env.cr.fetchone()
                    if result:
                        account_code = result[0]
                        _logger.info(f"Found custom account entry for account_id {custom_id} with code {account_code}")
                        
                        # Find the corresponding Odoo account by code
                        try:
                            odoo_account = env['account.account'].sudo().search([
                                ('code', '=', account_code)
                            ], limit=1)
                            if odoo_account:
                                id_mapping[custom_id] = odoo_account.id
                                _logger.info(f"Mapped custom account {custom_id} (code: {account_code}) to Odoo account {odoo_account.id}")
                            else:
                                _logger.warning(f"Custom account {custom_id} exists with code {account_code}, but no Odoo account found")
                        except Exception as e:
                            _logger.error(f"Error searching for Odoo account with code {account_code}: {str(e)}")
                    else:
                        _logger.debug(f"No custom account entry found for account_id {custom_id}")
                except Exception as e:
                    _logger.warning(f"SQL search failed for custom account {custom_id}: {e}")
            except Exception as e:
                _logger.error(f"Error processing custom account ID {custom_id}: {str(e)}")
    
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
