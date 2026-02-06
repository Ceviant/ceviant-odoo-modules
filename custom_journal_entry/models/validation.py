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
    
    The glAccountId values from journal entry are actually account codes (425, 680, etc).
    This function queries custom_account_entry by account_code, retrieves the account_id values,
    and maps them to Odoo account IDs.
    Returns a mapping of {glAccountId: odoo_account_id} for accounts that could be found.
    """
    if not account_ids:
        _logger.warning("No account IDs provided for validation")
        return {}
    
    try:
        # glAccountId values are account codes, convert to strings
        account_code_list = [str(id) for id in account_ids if id is not None]
        _logger.info(f"Validating account codes (glAccountId): {account_code_list}")
    except (ValueError, TypeError) as e:
        _logger.error(f"Error converting account codes to strings: {e}")
        return {}
    
    id_mapping = {}
    
    # Query custom_account_entry for the provided account codes
    try:
        env.cr.execute("""
            SELECT account_id, account_code FROM custom_account_entry 
            WHERE account_code IN %s
        """, (tuple(account_code_list),))
        custom_accounts = env.cr.fetchall()
        _logger.info(f"Found {len(custom_accounts)} custom account entries for codes {account_code_list}")
        
        # Build a mapping of account_code to account_code (for reference)
        custom_account_map = {row[1]: row[1] for row in custom_accounts}
        _logger.debug(f"Custom account mapping (code -> code): {custom_account_map}")
    except Exception as e:
        _logger.error(f"Error querying custom_account_entry: {e}")
        custom_account_map = {}
    
    # Validate provided account codes and map to Odoo accounts
    for original_id in account_ids:
        code_str = str(original_id)
        
        # Check if account_code exists in custom_account_entry
        if code_str in custom_account_map:
            _logger.info(f"Found custom account entry for account_code {code_str}")
            
            # Find the corresponding Odoo account by code using raw SQL
            # The 'code' field in account_account might not be searchable via ORM
            try:
                env.cr.execute("""
                    SELECT id FROM account_account 
                    WHERE code = %s
                    LIMIT 1
                """, (code_str,))
                result = env.cr.fetchone()
                
                if result:
                    odoo_account_id = result[0]
                    id_mapping[code_str] = odoo_account_id
                    _logger.info(f"Mapped glAccountId {code_str} to Odoo account {odoo_account_id}")
                else:
                    _logger.warning(f"Account code {code_str} found in custom_account_entry, but no Odoo account found")
            except Exception as e:
                _logger.error(f"Error searching for Odoo account with code {code_str}: {str(e)}")
        else:
            _logger.warning(f"Account code {code_str} not found in custom_account_entry")
    
    unmapped = [str(aid) for aid in account_ids if str(aid) not in id_mapping]
    if unmapped:
        _logger.error(f"Account validation failed for glAccountIds: {unmapped}")
    
    _logger.info(f"Account validation complete. Mapped {len(id_mapping)} of {len(account_ids)} accounts")
    return id_mapping


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
