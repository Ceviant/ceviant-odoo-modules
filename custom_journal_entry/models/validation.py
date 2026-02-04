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
    return 'NGN'


def get_currency_id(env, currency_code):
    """Get or create currency by code using proper environment.

    This function is a wrapper that delegates to journal_utils for the actual
    implementation to ensure a single source of truth and avoid circular imports.
    """
    try:
        # Get all currencies and find by code field (Odoo 18.0 uses different field names)
        all_currencies = env['res.currency'].sudo().search_read([], fields=['id'], order='id')

        # Try to find existing currency by checking each one
        for curr_record in all_currencies:
            curr = env['res.currency'].sudo().browse(curr_record['id'])
            # Check if this currency matches our code
            if hasattr(curr, 'name') and curr.name == currency_code:
                _logger.info(f"Found currency {currency_code} with ID {curr.id}")
                return curr.id
            elif hasattr(curr, 'code') and curr.code == currency_code:
                _logger.info(f"Found currency {currency_code} with ID {curr.id}")
                return curr.id

        # Create if not found - try different field names for Odoo 18.0
        _logger.info(f"Creating new currency {currency_code}")
        # Try creating with 'name' field first
        try:
            new_currency = env['res.currency'].sudo().create({'name': currency_code})
            _logger.info(f"Created currency {currency_code} with ID {new_currency.id}")
            return new_currency.id
        except Exception as create_error:
            _logger.warning(f"Failed to create with 'name' field: {create_error}")
            # Try with 'code' field
            try:
                new_currency = env['res.currency'].sudo().create({'code': currency_code})
                _logger.info(f"Created currency {currency_code} with ID {new_currency.id}")
                return new_currency.id
                _logger.error(f"Failed to create currency with any field: {create_error2}")
                return None

    except Exception as e:
        _logger.error(f"Currency lookup/creation failed for {currency_code}: {e}")
        import traceback
        _logger.error(f"Traceback: {traceback.format_exc()}")
        return None


def get_available_currencies():
    """Get list of all available currencies in the system."""
    env = request.env
    currencies = env['res.currency'].sudo().search_read([], fields=['id', 'code', 'symbol'])
    return [{'id': curr['id'], 'code': curr['code'], 'symbol': curr['symbol']} for curr in currencies]
