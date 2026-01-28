# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request, Response
from ..models.validation import validate_journal_entry
from ..models.rabbitmq_publisher import publish_journal_entry_to_rabbitmq, publish_journal_entry_update_to_rabbitmq
import logging
import json

_logger = logging.getLogger(__name__)

class JournalEntryController(http.Controller):
    @http.route('/ledger/transactions', type='http', auth='public', methods=['POST'], csrf=False)
    def handle_transaction(self):
        _logger.info("Entering handle_transaction method")
        raw_data = request.httprequest.data.decode('utf-8')
        try:
            payload = json.loads(raw_data)
            _logger.info(payload)
        except json.JSONDecodeError as e:
            _logger.error("Invalid JSON payload: %s", e)
            return Response(
                json.dumps({
                    "code": 400,
                    "status": "error",
                    "data": {
                        "message": "Invalid JSON payload",
                        "responseId": None
                    }
                }),
                status=400,
                content_type='application/json'
            )

        _logger.info("Received payload: %s", json.dumps(payload))
        valid, error = validate_journal_entry(payload)
        if not valid:
            return Response(
                json.dumps({
                    "code": 400,
                    "status": "error",
                    "data": {
                        "message": error,
                        "responseId": None
                    }
                }),
                status=400,
                content_type='application/json'
            )

        batch_ref = publish_journal_entry_to_rabbitmq(payload)
        if batch_ref:
            return Response(
                json.dumps({
                    "code": 202,
                    "status": "success",
                    "data": {
                        "message": "Request has been successfully logged.",
                        "responseId": batch_ref
                    }
                }),
                status=202,
                content_type='application/json'
            )
        else:
            return Response(
                json.dumps({
                    "code": 500,
                    "status": "error",
                    "data": {
                        "message": "Failed to publish to RabbitMQ",
                        "responseId": None
                    }
                }),
                status=500,
                content_type='application/json'
            )

    @http.route('/ledger/transactions', type='http', auth='public', methods=['PUT'], csrf=False)
    def update_transaction(self):
        _logger.info("Entering update_transaction method")
        raw_data = request.httprequest.data.decode('utf-8')
        try:
            payload = json.loads(raw_data)
            _logger.info(payload)
        except json.JSONDecodeError as e:
            _logger.error("Invalid JSON payload: %s", e)
            return Response(
                json.dumps({
                    "code": 400,
                    "status": "error",
                    "data": {
                        "message": "Invalid JSON payload",
                        "responseId": None
                    }
                }),
                status=400,
                content_type='application/json'
            )

        _logger.info("Received payload: %s", json.dumps(payload))
        valid, error = validate_journal_entry(payload)
        if not valid:
            return Response(
                json.dumps({
                    "code": 400,
                    "status": "error",
                    "data": {
                        "message": error,
                        "responseId": None
                    }
                }),
                status=400,
                content_type='application/json'
            )

        batch_ref = publish_journal_entry_update_to_rabbitmq(payload)
        if batch_ref:
            return Response(
                json.dumps({
                    "code": 202,
                    "status": "success",
                    "data": {
                        "message": "Update request has been successfully logged.",
                        "responseId": batch_ref
                    }
                }),
                status=202,
                content_type='application/json'
            )
        else:
            return Response(
                json.dumps({
                    "code": 500,
                    "status": "error",
                    "data": {
                        "message": "Failed to publish update to RabbitMQ",
                        "responseId": None
                    }
                }),
                status=500,
                content_type='application/json'
            )
