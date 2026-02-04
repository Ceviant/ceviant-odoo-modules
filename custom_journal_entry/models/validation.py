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
    """Validate that the provided account IDs exist in Odoo."""
    try:
        existing_records = env['account.account'].search([('id', 'in', list(account_ids))])
        return {rec.id for rec in existing_records}
    except Exception as e:
        _logger.error(f"Account validation failed: {e}")
        return set()


def get_default_currency():
    """Get Odoo's default currency or fallback to NGN."""
    try:
        env = request.env
        company = env['res.company'].sudo().search([], limit=1)
        if company and company.currency_id:
            return company.currency_id.code
    except Exception:
        pass
    return 'USD'


def get_currency_id(env, currency_code):
    """Get currency by code. Returns None if not found."""
    try:
        # Odoo 17: Override _order with explicit order clause
        currency_ids = env['res.currency'].sudo().search([], order='id')
        _logger.info(f"Currency IDs found: {currency_ids}")
        currencies = currency_ids.read(['id', 'name', 'code', 'iso_code', 'currency_code'])
        for currency in currencies:
            # Check each possible field
            for field in ['code', 'name', 'iso_code', 'currency_code']:
                if field in currency and currency[field]:
                    if str(currency[field]).upper() == currency_code.upper():
                        _logger.info(f"Found currency {currency_code} with ID {currency['id']} using '{field}'")
                        return currency['id']
        
        # If not found, try fallback to default currency
        _logger.warning(f"Currency {currency_code} not found, trying fallback to default currency")
        default_code = get_default_currency()
        for currency in currencies:
            for field in ['code', 'name', 'iso_code', 'currency_code']:
                if field in currency and currency[field]:
                    if str(currency[field]).upper() == default_code.upper():
                        _logger.info(f"Using fallback currency {default_code} with ID {currency['id']} using '{field}'")
                        return currency['id']
        
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
    # Odoo 17: Override _order with explicit order clause
    currency_ids = env['res.currency'].sudo().search([], order='id')
    currencies = currency_ids.read(['id', 'name', 'code', 'iso_code', 'currency_code', 'symbol'])
    result = []
    for curr in currencies:
        code = None
        for field in ['code', 'name', 'iso_code', 'currency_code']:
            if field in curr and curr[field]:
                code = str(curr[field])
                break
        if not code:
            code = str(curr.get('id', ''))
        symbol = curr.get('symbol', '')
        result.append({'id': curr['id'], 'code': code, 'symbol': symbol})
    return result
