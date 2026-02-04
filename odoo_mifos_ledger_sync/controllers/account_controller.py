from odoo import http
from odoo.http import request, Response
from ..models.validation import validate_account_entry, get_available_currencies, get_default_currency
from ..models.account_utils import get_account_records, get_account_data, create_account
import logging
import json

_logger = logging.getLogger(__name__)

class AccountEntryController(http.Controller):
    @http.route('/ledger/accounts', type='http', auth='public', methods=['POST'], csrf=False)
    def handle_account(self):
        _logger.info("\n" + "="*80)
        _logger.info(">>> API CALL: POST /ledger/accounts")
        _logger.info(f"Client IP: {request.httprequest.remote_addr}")
        _logger.info(f"Headers: {dict(request.httprequest.headers)}")
        
        raw_data = request.httprequest.data.decode('utf-8')
        _logger.info(f"Raw Request Body: {raw_data}")

        try:
            payload = json.loads(raw_data)
            # Default currency to Odoo's default currency if not provided
            if 'currency' not in payload or not payload['currency']:
                default_currency = get_default_currency()
                payload['currency'] = default_currency
                _logger.info(f"Currency not provided, using default: {default_currency}")
            _logger.info(f"Parsed JSON Payload: {json.dumps(payload, indent=2)}")
        except json.JSONDecodeError as e:
            _logger.error("Invalid JSON payload: %s", e)
            return Response(
                json.dumps({
                    "code": 400,
                    "status": "error",
                    "data": {"message": "Invalid JSON payload", "responseId": None}
                }),
                status=400,
                content_type='application/json'
            )

        if not validate_account_entry(payload):
            _logger.error(f"✗ Validation failed.")
            return Response(
                json.dumps({
                    "code": 400,
                    "status": "error",
                    "data": {"message": "Invalid account entry data", "responseId": None}
                }),
                status=400,
                content_type='application/json'
            )

        try:
            _logger.info(f"Creating account...")
            batch_ref, error = create_account(payload)
            if error:
                raise ValueError(error)
            _logger.info(f"✓ Account created successfully with batch_ref: {batch_ref}")
        except Exception as error:
            _logger.error(f"✗ Account creation failed: {error}")
            return Response(
                json.dumps({
                    "code": 400,
                    "status": "error",
                    "data": {"message": str(error), "responseId": None}
                }),
                status=400,
                content_type='application/json'
            )

        return Response(
            json.dumps({
                "code": 200,
                "status": "success",
                "data": {
                    "message": "Account creation request has been successfully logged.",
                    "responseId": batch_ref
                }
            }),
            status=200,
            content_type='application/json'
        )

    @http.route('/ledger/accounts', type='http', auth='public', methods=['GET'], csrf=False)
    def get_all_accounts_api(self, **kwargs):
        _logger.info("\n" + "="*80)
        _logger.info(">>> API CALL: GET /ledger/accounts")
        _logger.info(f"Client IP: {request.httprequest.remote_addr}")
        _logger.info(f"Fetching all account records...")

        try:
            accounts = get_account_records(request.env)
            account_data = get_account_data(request.env, accounts)
            _logger.info(f"✓ Successfully retrieved {len(account_data) if isinstance(account_data, list) else len(account_data.get('data', []))} accounts")
            _logger.info(f"Account data: {account_data}")
        except Exception as error:
            _logger.error(f"✗ Error fetching account data: {error}")
            return Response(
                json.dumps({
                    'code': 500,
                    'status': 'error',
                    'data': {"message": "Failed to retrieve account data"}
                }),
                status=500,
                content_type='application/json'
            )

        return Response(
            json.dumps({
                'code': 200,
                'status': 'success',
                'data': account_data
            }),
            status=200,
            content_type='application/json'
        )

    @http.route('/ledger/currencies', type='http', auth='public', methods=['GET'], csrf=False)
    def get_supported_currencies(self, **kwargs):
        _logger.info("Fetching all supported currencies")

        try:
            currencies = get_available_currencies()
            _logger.info(f"Found {len(currencies)} currencies")
        except Exception as error:
            _logger.error("Error fetching currencies: %s", error)
            return Response(
                json.dumps({
                    'code': 500,
                    'status': 'error',
                    'data': {"message": "Failed to retrieve currencies"}
                }),
                status=500,
                content_type='application/json'
            )

        return Response(
            json.dumps({
                'code': 200,
                'status': 'success',
                'data': currencies
            }),
            status=200,
            content_type='application/json'
        )
