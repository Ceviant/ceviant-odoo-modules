from odoo import http
from odoo.http import request, Response
from ..models.validation import validate_account_entry
from ..models.account_utils import get_account_records, get_account_data, create_account
import logging
import json

_logger = logging.getLogger(__name__)

class AccountEntryController(http.Controller):
    @http.route('/ledger/accounts', type='http', auth='public', methods=['POST'], csrf=False)
    def handle_account(self):
        _logger.info("Entering handle_account method")
        raw_data = request.httprequest.data.decode('utf-8')

        try:
            payload = json.loads(raw_data)
            _logger.info("Received payload: %s", payload)
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
            _logger.error("Payload validation failed.")
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
            batch_ref, error = create_account(payload)
            if error:
                raise ValueError(error)
        except Exception as error:
            _logger.error("Account creation failed: %s", error)
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
        _logger.info("Fetching all account records")

        try:
            accounts = get_account_records(request.env)
            account_data = get_account_data(request.env, accounts)
            _logger.info(f"Account data: {account_data}")
        except Exception as error:
            _logger.error("Error fetching account data: %s", error)
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
