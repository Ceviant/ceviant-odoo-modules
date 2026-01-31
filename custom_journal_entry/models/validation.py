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
    """Validate that the provided account IDs exist in Odoo"""
    try:
        _logger.info(f"Validating account IDs: {account_ids}")
        existing_records = env['account.account'].search([('id', 'in', list(account_ids))])
        existing_ids = [rec.id for rec in existing_records]
        _logger.info(f"Existing account IDs: {existing_ids}")
        return set(existing_ids)
    except Exception as e:
        _logger.error(f"Failed to validate account IDs in Odoo: {e}")
        return set()


def get_currency_id(currency_code):
    """Get the currency ID from the currency code."""
    env = request.env
    currency = env['res.currency'].sudo().search([('name', '=', currency_code)], limit=1)
    if currency:
        return currency.id
    else:
        _logger.error(f"Currency not found for code: {currency_code}")
        return None


def get_default_currency():
    """Get Odoo's default/base currency from the company."""
    env = request.env
    company = env['res.company'].sudo().search([], limit=1)
    if company and company.currency_id:
        return company.currency_id.name
    # Fallback to USD if no company currency found
    return 'USD'


def get_available_currencies():
    """Get list of all available currencies in the system."""
    env = request.env
    currencies = env['res.currency'].sudo().search_read([], fields=['id', 'name', 'symbol'])
    return [{'id': curr['id'], 'code': curr['name'], 'symbol': curr['symbol']} for curr in currencies]
