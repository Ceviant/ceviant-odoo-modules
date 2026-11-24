import jsonschema
from jsonschema import validate
import logging

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
        "account_status": {"type": "string"},
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


def validate_account_ids(env, gl_account_ids, company):
    """Map Fineract GL account ids to Odoo account ids within ``company``.

    Fineract's PostAccountsToOdoo job registers each GL account with
    account_code = str(GL id), so a transaction's glAccountId is looked up by
    that code. Only accounts registered through the sync (present in
    custom.account.entry) are accepted, so a Fineract id can never land on an
    unrelated chart account that happens to share the code.

    Returns {str(glAccountId): odoo_account_id} for the ids that resolve.
    """
    codes = sorted({str(g) for g in gl_account_ids if g is not None})
    if not codes:
        return {}
    registered = set(env['custom.account.entry'].sudo().search(
        [('account_code', 'in', codes)]).mapped('account_code'))
    accounts = env['account.account'].sudo().search([
        ('company_id', '=', company.id),
        ('code', 'in', [c for c in codes if c in registered]),
        ('deprecated', '=', False),
    ])
    return {a.code: a.id for a in accounts}


def get_currency_id(env, currency_code):
    """Return the id of the active currency with ISO code ``currency_code``."""
    if not currency_code:
        return None
    currency = env['res.currency'].sudo().search([('name', '=', currency_code.upper())], limit=1)
    return currency.id or None


def get_available_currencies(env):
    """List the active currencies."""
    currencies = env['res.currency'].sudo().search_read([], fields=['id', 'name', 'symbol'])
    return [{'id': c['id'], 'code': c['name'], 'symbol': c['symbol']} for c in currencies]
