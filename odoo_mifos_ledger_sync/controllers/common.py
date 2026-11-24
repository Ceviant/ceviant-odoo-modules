"""Shared plumbing for the ledger sync HTTP endpoints."""
import hmac
import json
import logging

from odoo.http import request, Response

from ..models.journal_utils import LedgerSyncError

_logger = logging.getLogger(__name__)

API_KEY_PARAM = 'ledger_sync.api_key'
API_KEY_HEADER = 'X-Ledger-Sync-Key'


def json_response(code, status, data):
    return Response(json.dumps({'code': code, 'status': status, 'data': data}),
                    status=code, content_type='application/json')


def error_response(code, message):
    return json_response(code, 'error', {'message': message, 'responseId': None})


def authorized():
    """Check the shared secret when the ``ledger_sync.api_key`` parameter is set.

    With the parameter unset the endpoints stay open, as before, so callers
    keep working until they are configured to send the header.
    """
    expected = request.env['ir.config_parameter'].sudo().get_param(API_KEY_PARAM)
    if not expected:
        return True
    supplied = request.httprequest.headers.get(API_KEY_HEADER) or ''
    return hmac.compare_digest(supplied.encode(), expected.encode())


def read_json():
    """Return the request body as a dict, or None if it is not a JSON object."""
    try:
        payload = json.loads(request.httprequest.get_data(as_text=True) or 'null')
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def run_sync(label, handler, payload):
    """Run ``handler(env, payload)`` as superuser inside a savepoint.

    Any failure rolls back everything the handler wrote, so an error response
    never leaves a half-created entry behind. Returns the handler's result, or
    an error Response.
    """
    env = request.env(su=True)
    try:
        with env.cr.savepoint():
            return handler(env, payload)
    except LedgerSyncError as e:
        _logger.warning("Ledger sync %s rejected: %s", label, e)
        return error_response(400, str(e))
    except Exception as e:  # noqa: BLE001 - report any ORM/DB failure to the caller
        _logger.exception("Ledger sync %s failed", label)
        return error_response(500, f"{type(e).__name__}: {e}")
