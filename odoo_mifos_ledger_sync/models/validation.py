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
    
    Accepts glAccountId values (which are account codes) from journal entry,
    queries custom_account_entry by account_code, retrieves account_ids,
    and maps them to Odoo account IDs.
    Returns a mapping of {original_id: odoo_account_id} for accounts that could be found.
    """
    if not account_ids:
        _logger.warning("No account IDs provided for validation")
        return {}
    
    try:
        # Convert to strings for account code matching
        account_code_list = [str(id) for id in account_ids if id is not None]
        _logger.info(f"Validating account codes: {account_code_list}")
    except (ValueError, TypeError) as e:
        _logger.error(f"Error converting account IDs to strings: {e}")
        return {}
    
    id_mapping = {}
    
    # Query custom_account_entry for the provided account codes
    try:
        env.cr.execute("""
            SELECT account_id, account_code FROM public.custom_account_entry 
            WHERE account_code IN %s
        """, (tuple(account_code_list),))
        custom_accounts = env.cr.fetchall()
        _logger.info(f"Found {len(custom_accounts)} custom account entries for codes {account_code_list}")
        
        # Build a mapping of account_code to custom account_id
        custom_account_map = {row[1]: row[0] for row in custom_accounts}
        _logger.debug(f"Custom account mapping: {custom_account_map}")
    except Exception as e:
        _logger.error(f"Error querying custom_account_entry: {e}")
        custom_account_map = {}
    
    # Validate provided account codes and map to Odoo
    for original_id in account_ids:
        code_str = str(original_id)
        
        # First, try to find accounts directly in account.account
        try:
            existing = env['account.account'].sudo().search([('id', '=', original_id)], limit=1)
            if existing:
                id_mapping[original_id] = existing.id
                _logger.debug(f"Found native Odoo account with ID {original_id}")
                continue
        except Exception as e:
            _logger.debug(f"Could not search for account ID {original_id}: {str(e)}")
        
        # Check if account_code exists in custom_account_entry
        if code_str in custom_account_map:
            custom_account_id = custom_account_map[code_str]
            _logger.info(f"Found custom account entry for code {code_str} with account_id {custom_account_id}")
            
            # Find the corresponding Odoo account by code
            try:
                odoo_account = env['account.account'].sudo().search([
                    ('code', '=', code_str)
                ], limit=1)
                if odoo_account:
                    id_mapping[original_id] = odoo_account.id
                    _logger.info(f"Mapped account code {code_str} to Odoo account {odoo_account.id}")
                else:
                    _logger.warning(f"Custom account with code {code_str} found, but no Odoo account found")
            except Exception as e:
                _logger.error(f"Error searching for Odoo account with code {code_str}: {str(e)}")
        else:
            _logger.warning(f"Account code {code_str} not found in custom_account_entry")
    
    unmapped = [aid for aid in account_ids if aid not in id_mapping]
    if unmapped:
        _logger.error(f"Account validation failed for codes: {unmapped}")
    
    _logger.info(f"Account validation complete. Mapped {len(id_mapping)} of {len(account_ids)} accounts")
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
