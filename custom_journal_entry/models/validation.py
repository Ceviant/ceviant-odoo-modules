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
    """Validate that the provided account IDs exist in Odoo.
    
    First checks if the IDs exist directly in account.account.
    If not found, attempts to map them from custom.account.entry.
    """
    if not account_ids:
        _logger.warning("No account IDs provided for validation")
        return set()
    
    # Convert all IDs to integers to handle float/string inputs from JSON
    account_id_list = [int(id) for id in account_ids if id is not None]
    _logger.debug(f"Validating account IDs: {account_id_list}")
    
    valid_ids = set()
    
    # First, try to find accounts directly in account.account (native Odoo accounts)
    # Check each ID individually to avoid domain parsing issues with non-existent IDs
    for acc_id in account_id_list:
        try:
            existing = env['account.account'].sudo().search([('id', '=', acc_id)], limit=1)
            if existing:
                valid_ids.add(acc_id)
                _logger.debug(f"Found native Odoo account with ID {acc_id}")
        except Exception as e:
            _logger.debug(f"Could not search for account ID {acc_id}: {str(e)}")
    
    # If some IDs are missing, try to map them from custom.account.entry
    invalid_ids = set(account_id_list) - valid_ids
    if invalid_ids:
        _logger.info(f"Attempting to map custom account IDs: {invalid_ids}")
        
        try:
            # Search for custom account entries with the given account_id values
            # This maps the custom account IDs to the corresponding Odoo account.account records
            for custom_id in invalid_ids:
                custom_entries = env['custom.account.entry'].sudo().search([
                    ('account_id', '=', str(custom_id))
                ])
                
                if custom_entries:
                    _logger.debug(f"Found custom account entry for ID {custom_id}")
                    # Try to find corresponding Odoo accounts by code
                    for entry in custom_entries:
                        try:
                            odoo_account = env['account.account'].sudo().search([
                                ('code', '=', entry.account_code)
                            ], limit=1)
                            if odoo_account:
                                valid_ids.add(odoo_account.id)
                                _logger.info(f"Mapped custom account {entry.account_id} (code: {entry.account_code}) to Odoo account {odoo_account.id}")
                            else:
                                _logger.warning(f"Could not find Odoo account for custom account {entry.account_id} with code {entry.account_code}")
                        except Exception as e:
                            _logger.warning(f"Error searching for Odoo account with code {entry.account_code}: {str(e)}")
                else:
                    _logger.debug(f"No custom account entry found for ID {custom_id}")
        except Exception as e:
            _logger.warning(f"Error searching custom account entries: {str(e)}")
    
    remaining_invalid = set(account_id_list) - valid_ids
    if remaining_invalid:
        _logger.warning(f"Invalid account IDs that could not be validated or mapped: {remaining_invalid}")
    
    _logger.info(f"Account validation complete. Valid IDs: {valid_ids}, Invalid IDs: {remaining_invalid}")
    return valid_ids


def get_default_currency():
    """Get Odoo's default currency or fallback to NGN."""
    try:
        env = request.env
        company = env['res.company'].sudo().search([], limit=1)
        if company and company.currency_id:
            return company.currency_id.code
    except Exception:
        pass
    return 'NGN'


def get_currency_id(env, currency_code):
    """Get currency by code. Returns None if not found."""
    try:
        # Bypass Odoo's broken _order by using raw SQL with filter on name
        env.cr.execute("""
            SELECT id FROM res_currency 
            WHERE UPPER(name) = %s AND active = true
            LIMIT 1
        """, (currency_code.upper(),))
        result = env.cr.fetchone()
        
        if result:
            _logger.info(f"Found currency {currency_code} with ID {result[0]}")
            return result[0]
        
        # If not found, try fallback to default currency
        _logger.warning(f"Currency {currency_code} not found, trying fallback to default currency")
        default_code = get_default_currency()
        env.cr.execute("""
            SELECT id FROM res_currency 
            WHERE UPPER(name) = %s AND active = true
            LIMIT 1
        """, (default_code.upper(),))
        result = env.cr.fetchone()
        
        if result:
            _logger.info(f"Using fallback currency {default_code} with ID {result[0]}")
            return result[0]
        
        _logger.warning(f"Currency {currency_code} and default {default_code} not found")
        return None
    except Exception as e:
        _logger.error(f"Currency lookup failed for {currency_code}: {e}")
        import traceback
        _logger.error(f"Traceback: {traceback.format_exc()}")
        return None


def get_available_currencies():
    """Get list of all available currencies in the system."""
    env = request.env
    # Bypass Odoo's broken _order by using raw SQL
    env.cr.execute("""
        SELECT id, name, code, iso_code, symbol 
        FROM res_currency 
        ORDER BY id
    """)
    currencies = env['res.currency'].browse([c[0] for c in env.cr.fetchall()])
    result = []
    for curr in currencies:
        code = None
        for field in ['code', 'iso_code']:
            value = getattr(curr, field, None)
            if value:
                code = str(value)
                break
        if not code:
            code = str(curr.id)
        symbol = getattr(curr, 'symbol', '') or ''
        result.append({'id': curr.id, 'code': code, 'symbol': symbol})
    return result
