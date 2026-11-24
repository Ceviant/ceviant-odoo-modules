"""Optional RabbitMQ consumer for ledger sync messages.

Fineract posts to the HTTP endpoints synchronously, so this consumer is only
needed to drain messages that something else publishes to the queues. It is
off unless the ``LEDGER_SYNC_RABBITMQ_CONSUMER`` environment variable is
``1``/``true``.

Each consumer thread owns its own connection and handles messages on that
thread (pika's BlockingConnection is not thread-safe), and each message runs
in its own database cursor that is committed on success and rolled back on
failure.
"""
import json
import logging
import os
import threading
import time

from odoo import SUPERUSER_ID, api, models
from odoo.modules.registry import Registry

from .account_utils import create_account
from .journal_utils import LedgerSyncError, process_transaction, update_transaction

_logger = logging.getLogger(__name__)

ENABLE_ENV = 'LEDGER_SYNC_RABBITMQ_CONSUMER'
MAX_RETRIES = 3
RETRY_BACKOFF = [1, 3, 10]
NUM_CONSUMERS = 2

HANDLERS = {
    'odoo_transaction_queue': process_transaction,
    'odoo_account_queue': create_account,
    'odoo_update_journal_queue': update_transaction,
}
FAILURE_QUEUES = {queue: queue.replace('_queue', '_failure_queue') for queue in HANDLERS}


def consumer_enabled():
    return os.getenv(ENABLE_ENV, '').strip().lower() in ('1', 'true', 'yes')


def _connection_params():
    import pika
    return pika.ConnectionParameters(
        host=os.getenv('RABBITMQ_HOST', 'rabbitmq'),
        port=int(os.getenv('RABBITMQ_PORT') or 5672),
        virtual_host=os.getenv('RABBITMQ_VHOST', '/'),
        credentials=pika.PlainCredentials(os.getenv('RABBITMQ_USERNAME', 'admin'),
                                          os.getenv('RABBITMQ_PASSWORD', 'admin')),
        connection_attempts=3, retry_delay=1, heartbeat=30,
    )


def _run_handler(dbname, handler, payload):
    """Run one handler in its own transaction (commit on success, rollback on error)."""
    with Registry(dbname).cursor() as cr:
        handler(api.Environment(cr, SUPERUSER_ID, {}), payload)


def _consume_forever(dbname, name):
    import pika
    backoff = 1
    while True:
        try:
            connection = pika.BlockingConnection(_connection_params())
            channel = connection.channel()
            channel.basic_qos(prefetch_count=1)
            for queue in list(HANDLERS) + list(FAILURE_QUEUES.values()):
                channel.queue_declare(queue=queue, durable=True)

            def on_message(ch, method, _properties, body, queue):
                handle_message(dbname, ch, method, body, queue)

            for queue in HANDLERS:
                channel.basic_consume(
                    queue=queue,
                    on_message_callback=lambda ch, m, p, b, q=queue: on_message(ch, m, p, b, q))
            _logger.info("%s consuming %s on %s", name, list(HANDLERS), dbname)
            backoff = 1
            channel.start_consuming()
        except Exception:  # noqa: BLE001 - keep the consumer alive across broker outages
            _logger.exception("%s lost its RabbitMQ connection; reconnecting in %ss", name, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)


def handle_message(dbname, ch, method, body, queue):
    """Process one message, retrying transient failures, then ack it.

    Payload problems (LedgerSyncError, bad JSON) are not retried. Anything
    that still fails is copied to the queue's failure queue before the ack,
    so nothing is dropped silently.
    """
    import pika
    error = None
    try:
        message = json.loads(body)
        payload = message.get('payload', message)
        for attempt in range(MAX_RETRIES + 1):
            try:
                _run_handler(dbname, HANDLERS[queue], payload)
                error = None
                break
            except LedgerSyncError as e:
                error = e
                break
            except Exception as e:  # noqa: BLE001 - retried, then dead-lettered
                error = e
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF[attempt])
    except ValueError as e:
        error = e

    if error is not None:
        _logger.error("Ledger sync message on %s failed: %s", queue, error)
        ch.basic_publish(exchange='', routing_key=FAILURE_QUEUES[queue], body=body,
                         properties=pika.BasicProperties(delivery_mode=2))
    ch.basic_ack(delivery_tag=method.delivery_tag)


class BatchProcessor(models.Model):
    _name = 'custom_journal_entry.batch_processor'
    _description = 'Ledger sync RabbitMQ consumer'

    _started_dbs = set()
    _start_lock = threading.Lock()

    @api.model
    def run_batch_processor(self):
        """Start the consumer threads for this database once per process."""
        if not consumer_enabled():
            _logger.info("Ledger sync RabbitMQ consumer disabled (set %s=1 to enable)", ENABLE_ENV)
            return
        dbname = self.env.cr.dbname
        with self._start_lock:
            if dbname in self._started_dbs:
                return
            self._started_dbs.add(dbname)
        for i in range(NUM_CONSUMERS):
            threading.Thread(target=_consume_forever, args=(dbname, f'LedgerSyncConsumer-{i + 1}'),
                             daemon=True, name=f'LedgerSyncConsumer-{i + 1}').start()
