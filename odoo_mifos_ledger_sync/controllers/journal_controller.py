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
        _logger.info("\n" + "="*80)
        _logger.info(">>> API CALL: POST /ledger/transactions")
        _logger.info(f"Client IP: {request.httprequest.remote_addr}")
        _logger.info(f"Headers: {dict(request.httprequest.headers)}")
        
        raw_data = request.httprequest.data.decode('utf-8')
        _logger.info(f"Raw Request Body: {raw_data}")
        
        try:
            payload = json.loads(raw_data)
            _logger.info(f"Parsed JSON Payload: {json.dumps(payload, indent=2)}")
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

        _logger.info(f"Validating journal entry payload...")
        valid, error = validate_journal_entry(payload)
        if not valid:
            _logger.error(f"✗ Validation failed: {error}")
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

        _logger.info(f"✓ Validation passed")
        _logger.info(f"Publishing to RabbitMQ...")
        batch_ref = publish_journal_entry_to_rabbitmq(payload)
        if batch_ref:
            _logger.info(f"✓ Successfully published with batch_ref: {batch_ref}")
            _logger.info(f"<<< API RESPONSE: 202 Accepted (batch_ref: {batch_ref})")
            _logger.info("="*80 + "\n")
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
            _logger.error(f"✗ Failed to publish to RabbitMQ")
            _logger.info(f"<<< API RESPONSE: 500 Internal Server Error")
            _logger.info("="*80 + "\n")
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
        _logger.info("\n" + "="*80)
        _logger.info(">>> API CALL: PUT /ledger/transactions")
        _logger.info(f"Client IP: {request.httprequest.remote_addr}")
        _logger.info(f"Headers: {dict(request.httprequest.headers)}")
        
        raw_data = request.httprequest.data.decode('utf-8')
        _logger.info(f"Raw Request Body: {raw_data}")
        try:
            payload = json.loads(raw_data)
            _logger.info(f"Parsed JSON Payload: {json.dumps(payload, indent=2)}")
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
