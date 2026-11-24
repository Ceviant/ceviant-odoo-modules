# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import Response

from ..models.journal_utils import process_transaction, update_transaction
from .common import authorized, error_response, json_response, read_json, run_sync


class JournalEntryController(http.Controller):
    """Endpoints called by Fineract's PostJournalsToOdoo job.

    Entries are created and posted synchronously, so a success response means
    the entry is committed and posted in Odoo. Fineract only stores the
    returned responseId on success; on any error it leaves the journal
    unmarked and resends it on its next run.
    """

    @http.route('/ledger/transactions', type='http', auth='public', methods=['POST'], csrf=False)
    def handle_transaction(self):
        if not authorized():
            return error_response(401, 'Unauthorized')
        payload = read_json()
        if payload is None:
            return error_response(400, 'Invalid JSON payload')

        result = run_sync('create transaction', process_transaction, payload)
        if isinstance(result, Response):
            return result
        move, created = result
        return json_response(202 if created else 200, 'success', {
            'message': 'Transaction posted.' if created else 'Transaction already posted.',
            'responseId': payload.get('transactionReference'),
            'odooMove': move.name,
        })

    @http.route('/ledger/transactions', type='http', auth='public', methods=['PUT'], csrf=False)
    def update_transaction(self):
        if not authorized():
            return error_response(401, 'Unauthorized')
        payload = read_json()
        if payload is None:
            return error_response(400, 'Invalid JSON payload')

        result = run_sync('update transaction', update_transaction, payload)
        if isinstance(result, Response):
            return result
        return json_response(202, 'success', {
            'message': 'Transaction updated and re-posted.',
            'responseId': payload.get('transactionReference'),
            'odooMove': result.name,
        })
