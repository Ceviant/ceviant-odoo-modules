# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request, Response

from ..models.account_utils import create_account, get_account_data, get_account_records
from ..models.validation import get_available_currencies
from .common import authorized, error_response, json_response, read_json, run_sync


class AccountEntryController(http.Controller):
    """Endpoints called by Fineract's PostAccountsToOdoo job."""

    @http.route('/ledger/accounts', type='http', auth='public', methods=['POST'], csrf=False)
    def handle_account(self):
        if not authorized():
            return error_response(401, 'Unauthorized')
        payload = read_json()
        if payload is None:
            return error_response(400, 'Invalid JSON payload')

        result = run_sync('create account', create_account, payload)
        if isinstance(result, Response):
            return result
        account, created = result
        return json_response(200, 'success', {
            'message': 'Account created.' if created else 'Account already exists.',
            'responseId': f'odoo-account-{account.id}',
        })

    @http.route('/ledger/accounts', type='http', auth='public', methods=['GET'], csrf=False)
    def get_all_accounts_api(self, **kwargs):
        if not authorized():
            return error_response(401, 'Unauthorized')
        accounts = get_account_records(request.env(su=True))
        return json_response(200, 'success', get_account_data(accounts))

    @http.route('/ledger/currencies', type='http', auth='public', methods=['GET'], csrf=False)
    def get_supported_currencies(self, **kwargs):
        if not authorized():
            return error_response(401, 'Unauthorized')
        return json_response(200, 'success', get_available_currencies(request.env(su=True)))
