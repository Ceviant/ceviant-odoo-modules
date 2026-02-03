import pika
import json
import logging
import os
from odoo import models, api
from .journal_utils import process_transaction, update_journal_entry_in_database
from .account_utils import create_account
import time
from threading import Thread
from concurrent.futures import ThreadPoolExecutor
import threading

# Try to import Loki handler, fallback to manual if not available
try:
    from loki_handler import LokiHandler
    LOKI_AVAILABLE = True
except ImportError:
    LOKI_AVAILABLE = False

# Configure logging with file handler and Loki handler
def setup_logger():
    """Setup logger with console, file, and Loki handlers."""
    logger = logging.getLogger('rabbitmq_consumer')
    logger.setLevel(logging.INFO)  # Changed to INFO (reduced verbosity)
    
    # Clear existing handlers
    logger.handlers.clear()
    
    # Create logs directory if it doesn't exist
    log_dir = '/var/log/odoo' if os.path.exists('/var/log/odoo') or os.access('/var/log', os.W_OK) else '/tmp'
    log_file = os.path.join(log_dir, 'rabbitmq_consumer.log')
    
    # File handler - logs everything
    try:
        fh = logging.FileHandler(log_file)
        fh.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        fh.setFormatter(file_formatter)
        logger.addHandler(fh)
    except Exception:
        pass
    
    # Console handler - logs warning and above
    ch = logging.StreamHandler()
    ch.setLevel(logging.WARNING)
    console_formatter = logging.Formatter('%(levelname)s - %(message)s')
    ch.setFormatter(console_formatter)
    logger.addHandler(ch)
    
    # Loki handler - push logs to Loki using python-loki
    if LOKI_AVAILABLE:
        loki_url = os.getenv("LOKI_URL", "http://loki.monitoring:3100")
        try:
            loki_handler = LokiHandler(
                url=loki_url,
                tags={"job": "rabbitmq_consumer", "service": "odoo_mifos_ledger_sync"},
                version="1"
            )
            loki_handler.setLevel(logging.INFO)
            logger.addHandler(loki_handler)
        except Exception:
            pass
    
    return logger

_logger = setup_logger()

MAX_RETRIES = 3  # Reduced from 5
RETRY_BACKOFF = [0, 1, 3]  # Exponential backoff (0s, 1s, 3s)
PREFETCH_COUNT = 10  # Increased from 1 for better throughput
THREAD_POOL_SIZE = 5  # Process 5 messages in parallel

# Handler mapping for faster lookups
HANDLER_MAP = {
    'odoo_transaction_queue': ('Processing transaction', process_transaction),
    'odoo_account_queue': ('Creating account', create_account),
    'odoo_update_journal_queue': ('Updating journal', update_journal_entry_in_database),
}

FAILURE_QUEUE_MAP = {
    'odoo_transaction_queue': 'odoo_transaction_failure_queue',
    'odoo_account_queue': 'odoo_account_failure_queue',
    'odoo_update_journal_queue': 'odoo_update_journal_failure_queue'
}

class BatchProcessor(models.Model):
    _name = 'custom_journal_entry.batch_processor'
    _connection = None
    _channel = None
    _lock = threading.Lock()

    @classmethod
    def get_connection(cls):
        """Get or create RabbitMQ connection (singleton pattern)."""
        if cls._connection is None or cls._connection.is_closed:
            host = os.getenv("RABBITMQ_HOST", "rabbitmq")
            port = int(os.getenv("RABBITMQ_PORT", "5672"))
            virtual_host = os.getenv("RABBITMQ_VHOST", "/")
            username = os.getenv("RABBITMQ_USERNAME", "guest")
            password = os.getenv("RABBITMQ_PASSWORD", "guest")
            
            params = pika.ConnectionParameters(
                host=host, port=port, virtual_host=virtual_host,
                credentials=pika.PlainCredentials(username, password),
                connection_attempts=3, retry_delay=2,
                socket_options=[(1, 9, 1)]  # TCP_NODELAY for lower latency
            )
            cls._connection = pika.BlockingConnection(params)
        return cls._connection

    @classmethod
    def get_channel(cls):
        """Get or create channel (reuse connection)."""
        if cls._channel is None or cls._channel.is_closed:
            cls._channel = cls.get_connection().channel()
            cls._channel.basic_qos(prefetch_count=PREFETCH_COUNT)
            # Declare all queues once
            for q in ['odoo_transaction_queue', 'odoo_account_queue', 'odoo_update_journal_queue',
                     'odoo_transaction_failure_queue', 'odoo_account_failure_queue', 'odoo_update_journal_failure_queue']:
                cls._channel.queue_declare(queue=q, durable=True)
        return cls._channel

    def process_message(self, ch, method, body, retry_count=0):
        """Process single message with optimized error handling."""
        batch_ref = None
        try:
            message = json.loads(body)
            batch_ref = message.get('batch_ref')
            payload = message.get('payload')
            queue_type = method.routing_key
            
            if queue_type not in HANDLER_MAP:
                _logger.warning(f"Unknown queue type: {queue_type} batch {batch_ref}")
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                return
            
            label, handler = HANDLER_MAP[queue_type]
            _logger.debug(f"{label} batch {batch_ref}")
            if handler(payload):
                _logger.info(f"✓ batch {batch_ref} processed")
                ch.basic_ack(delivery_tag=method.delivery_tag)
            else:
                raise Exception(f"{label} failed")
                
        except json.JSONDecodeError as e:
            _logger.error(f"JSON error batch {batch_ref}: {e}")
            self.retry_or_fail(ch, method, body, retry_count)
        except Exception as e:
            _logger.error(f"Error batch {batch_ref}: {e}")
            self.retry_or_fail(ch, method, body, retry_count)

    def retry_or_fail(self, ch, method, body, retry_count=0):
        """Retry with exponential backoff or move to failure queue."""
        if retry_count < MAX_RETRIES:
            delay = RETRY_BACKOFF[retry_count]
            batch_ref = json.loads(body).get('batch_ref')
            _logger.warning(f"Retry {retry_count+1}/{MAX_RETRIES} batch {batch_ref} (wait {delay}s)")
            time.sleep(delay)
            self.process_message(ch, method, body, retry_count + 1)
        else:
            batch_ref = json.loads(body).get('batch_ref')
            queue_type = method.routing_key
            failure_queue = FAILURE_QUEUE_MAP.get(queue_type)
            if failure_queue:
                _logger.error(f"Failed batch {batch_ref} → {failure_queue}")
                ch.basic_publish(exchange='', routing_key=failure_queue, body=body)
            ch.basic_ack(delivery_tag=method.delivery_tag)

    def fetch_and_process_messages(self):
        """Continuous consumer with thread pool for parallel processing."""
        try:
            channel = self.get_channel()
            executor = ThreadPoolExecutor(max_workers=THREAD_POOL_SIZE)
            
            def callback(ch, method, properties, body):
                # Set queue type on method for handler routing
                method.routing_key = method.routing_key or self._infer_queue_type(body)
                # Process in thread pool (non-blocking)
                executor.submit(self.process_message, ch, method, body, 0)
            
            for queue_name in ['odoo_transaction_queue', 'odoo_account_queue', 'odoo_update_journal_queue']:
                channel.basic_consume(queue=queue_name, on_message_callback=callback)
            
            _logger.info(f"🚀 Consumer ready: prefetch={PREFETCH_COUNT} workers={THREAD_POOL_SIZE} retry_limit={MAX_RETRIES}")
            channel.start_consuming()
            
        except Exception as e:
            _logger.critical(f"Consumer fatal error: {e}", exc_info=True)
            raise

    @staticmethod
    def _infer_queue_type(body):
        """Fast queue type inference."""
        try:
            body_str = body.decode('utf-8', errors='ignore').lower()
            for queue_type in ['transaction', 'account', 'update_journal']:
                if queue_type in body_str:
                    return f"{queue_type}_queue"
        except:
            pass
        return None

    @api.model
    def run_batch_processor(self):
        """Run the batch processor as background service."""
        # Start consumer in daemon thread
        consumer_thread = Thread(target=self.fetch_and_process_messages, daemon=True)
        consumer_thread.start()
        _logger.info("Batch processor started")
