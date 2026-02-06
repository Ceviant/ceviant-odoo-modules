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
NUM_CONSUMERS = 2  # Number of concurrent consumers

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
    _consumer_active = False
    _lock = threading.Lock()
    _active_consumers = 0  # Track number of active consumers
    _consumers_lock = threading.Lock()  # Lock for consumer count updates

    @classmethod
    def get_connection(cls):
        """Get or create RabbitMQ connection (singleton pattern)."""
        if cls._connection is None or cls._connection.is_closed:
            try:
                host = os.getenv("RABBITMQ_HOST", "rabbitmq")
                port = int(os.getenv("RABBITMQ_PORT", "5672"))
                virtual_host = os.getenv("RABBITMQ_VHOST", "/")
                username = os.getenv("RABBITMQ_USERNAME", "admin")
                password = os.getenv("RABBITMQ_PASSWORD", "admin")
                
                _logger.info(f"Connecting to RabbitMQ: {host}:{port} (vhost: {virtual_host})")
                
                params = pika.ConnectionParameters(
                    host=host, port=port, virtual_host=virtual_host,
                    credentials=pika.PlainCredentials(username, password),
                    connection_attempts=3,  # Reduced from 5
                    retry_delay=1,  # Reduced from 2
                    socket_options=[(1, 9, 1)],  # TCP_NODELAY for lower latency
                    connection_timeout=5,  # Add explicit timeout
                    heartbeat=30  # Keep connection alive
                )
                cls._connection = pika.BlockingConnection(params)
                _logger.info(f"✓ Connected to RabbitMQ successfully")
            except Exception as e:
                _logger.error(f"✗ Failed to connect to RabbitMQ: {e}")
                raise
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
        """Process single message with optimized error handling and detailed logging."""
        batch_ref = None
        try:
            message = json.loads(body)
            batch_ref = message.get('batch_ref')
            payload = message.get('payload')
            queue_type = method.routing_key
            
            _logger.info(f"\n{'='*80}")
            _logger.info(f">>> DEQUEUE EVENT")
            _logger.info(f"Queue: {queue_type}")
            _logger.info(f"Batch Reference: {batch_ref}")
            _logger.info(f"Delivery Tag: {method.delivery_tag}")
            _logger.info(f"Retry Attempt: {retry_count + 1}/{MAX_RETRIES + 1}")
            _logger.info(f"Payload: {json.dumps(payload, indent=2) if payload else 'None'}")
            
            if queue_type not in HANDLER_MAP:
                _logger.error(f"✗ Unknown queue type: {queue_type} batch {batch_ref}")
                _logger.error(f"Available queues: {list(HANDLER_MAP.keys())}")
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                _logger.info(f"Message REJECTED (unknown queue type)")
                _logger.info(f"{'='*80}\n")
                return
            
            label, handler = HANDLER_MAP[queue_type]
            _logger.info(f"Processing: {label}")
            
            handler_result = handler(payload)
            if handler_result:
                _logger.info(f"✓ Handler executed successfully for batch {batch_ref}")
                ch.basic_ack(delivery_tag=method.delivery_tag)
                _logger.info(f"Message ACKNOWLEDGED and removed from queue")
                _logger.info(f"<<< DEQUEUE SUCCESS")
                _logger.info(f"{'='*80}\n")
            else:
                _logger.error(f"✗ Handler returned false: {label} failed for batch {batch_ref}")
                raise Exception(f"{label} failed - handler returned false")
                
        except json.JSONDecodeError as e:
            _logger.error(f"✗ JSON Decode Error for batch {batch_ref}: {str(e)}")
            _logger.error(f"Raw Body: {body[:200]}")
            self.retry_or_fail(ch, method, body, retry_count)
        except Exception as e:
            _logger.error(f"✗ Processing Error for batch {batch_ref}: {type(e).__name__} - {str(e)}")
            import traceback
            _logger.error(f"Traceback: {traceback.format_exc()}")
            self.retry_or_fail(ch, method, body, retry_count)

    def retry_or_fail(self, ch, method, body, retry_count=0):
        """Retry with exponential backoff or move to failure queue with detailed logging."""
        try:
            batch_ref = json.loads(body).get('batch_ref')
            queue_type = method.routing_key
        except:
            batch_ref = 'UNKNOWN'
            queue_type = 'UNKNOWN'
            
        if retry_count < MAX_RETRIES:
            delay = RETRY_BACKOFF[retry_count]
            _logger.warning(f"✗ RETRY SCHEDULED")
            _logger.warning(f"Batch: {batch_ref}")
            _logger.warning(f"Attempt: {retry_count + 1}/{MAX_RETRIES}")
            _logger.warning(f"Backoff Delay: {delay}s")
            _logger.warning(f"Next retry in {delay} seconds...")
            _logger.info(f"{'='*80}\n")
            time.sleep(delay)
            self.process_message(ch, method, body, retry_count + 1)
        else:
            failure_queue = FAILURE_QUEUE_MAP.get(queue_type)
            _logger.error(f"✗ MAX RETRIES EXCEEDED")
            _logger.error(f"Batch: {batch_ref}")
            _logger.error(f"Queue: {queue_type}")
            _logger.error(f"Attempts Made: {MAX_RETRIES + 1}")
            
            if failure_queue:
                _logger.error(f"Moving to Failure Queue: {failure_queue}")
                try:
                    ch.basic_publish(exchange='', routing_key=failure_queue, body=body, properties=pika.BasicProperties(delivery_mode=2))
                    _logger.error(f"✓ Message moved to failure queue successfully")
                except Exception as e:
                    _logger.error(f"✗ Failed to move message to failure queue: {e}")
            else:
                _logger.error(f"✗ No failure queue configured for {queue_type}")
            
            # Acknowledge to remove from original queue
            try:
                ch.basic_ack(delivery_tag=method.delivery_tag)
                _logger.error(f"Message ACKNOWLEDGED and removed from original queue")
            except Exception as e:
                _logger.error(f"✗ Failed to acknowledge message: {e}")
            
            _logger.error(f"<<< DEQUEUE FAILED (exhausted retries)")
            _logger.error(f"{'='*80}\n")

    def fetch_and_process_messages(self):
        """Continuous consumer with thread pool for parallel processing and auto-reconnection."""
        consumer_id = threading.current_thread().name
        
        # Increment active consumer count
        with self._consumers_lock:
            BatchProcessor._active_consumers += 1
            active_count = BatchProcessor._active_consumers
        
        _logger.info(f"\n{consumer_id} - Starting fetch_and_process_messages")
        _logger.info(f"{consumer_id} - Active Consumers: {active_count}/{NUM_CONSUMERS}")
        
        initial_backoff = 1  # Start with 1 second backoff
        max_backoff = 60  # Max 60 seconds
        current_backoff = initial_backoff
        
        while True:  # Keep running indefinitely
            try:
                _logger.info(f"{consumer_id} - Initializing consumer...")
                channel = self.get_channel()
                executor = ThreadPoolExecutor(max_workers=THREAD_POOL_SIZE)
                
                def callback(ch, method, properties, body):
                    try:
                        # Set queue type on method for handler routing
                        method.routing_key = method.routing_key or self._infer_queue_type(body)
                        # Process in thread pool (non-blocking)
                        executor.submit(self.process_message, ch, method, body, 0)
                    except Exception as callback_error:
                        _logger.error(f"Error in callback function: {callback_error}", exc_info=True)
                        try:
                            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                        except Exception as nack_error:
                            _logger.error(f"Failed to nack message: {nack_error}")
                
                queues = ['odoo_transaction_queue', 'odoo_account_queue', 'odoo_update_journal_queue']
                for queue_name in queues:
                    channel.basic_consume(queue=queue_name, on_message_callback=callback)
                
                _logger.info(f"\n{'='*80}")
                _logger.info(f"🚀 {consumer_id} STARTED")
                _logger.info(f"Consumer Status: {active_count}/{NUM_CONSUMERS} ACTIVE")
                _logger.info(f"Thread Pool Size: {THREAD_POOL_SIZE} workers")
                _logger.info(f"Prefetch Count: {PREFETCH_COUNT} messages")
                _logger.info(f"Max Retries: {MAX_RETRIES}")
                _logger.info(f"Listening on Queues: {', '.join(queues)}")
                _logger.info(f"Status: CONSUMING MESSAGES")
                _logger.info(f"{'='*80}")
                _logger.info(f"✓ {consumer_id} is now READY and listening for messages\n")
                
                # Reset backoff on successful connection
                current_backoff = initial_backoff
                
                # This blocks until connection drops
                try:
                    channel.start_consuming()
                except KeyboardInterrupt:
                    _logger.info(f"{consumer_id} - Keyboard interrupt received")
                    channel.stop_consuming()
                except Exception as consume_error:
                    _logger.error(f"{consumer_id} - Error during consuming: {consume_error}", exc_info=True)
                    raise
                
            except pika.exceptions.ConnectionClosedByBroker:
                _logger.warning(f"{consumer_id} - Connection closed by broker, reconnecting in {current_backoff}s...")
                _logger.warning(f"{consumer_id} - Active Consumers: {active_count}/{NUM_CONSUMERS}")
                time.sleep(current_backoff)
                self._channel = None
                self._connection = None
                current_backoff = min(current_backoff * 2, max_backoff)
                
            except pika.exceptions.AMQPChannelError as e:
                _logger.error(f"{consumer_id} - AMQP Channel Error: {e}, reconnecting in {current_backoff}s...")
                _logger.error(f"{consumer_id} - Active Consumers: {active_count}/{NUM_CONSUMERS}")
                time.sleep(current_backoff)
                self._channel = None
                self._connection = None
                current_backoff = min(current_backoff * 2, max_backoff)
                
            except pika.exceptions.AMQPConnectionError as e:
                _logger.error(f"{consumer_id} - AMQP Connection Error: {e}, reconnecting in {current_backoff}s...")
                _logger.error(f"{consumer_id} - Active Consumers: {active_count}/{NUM_CONSUMERS}")
                time.sleep(current_backoff)
                self._channel = None
                self._connection = None
                current_backoff = min(current_backoff * 2, max_backoff)
                
            except Exception as e:
                _logger.critical(f"\n{consumer_id} - FATAL ERROR: {type(e).__name__} - {str(e)}")
                _logger.critical(f"Traceback: ", exc_info=True)
                _logger.critical(f"{consumer_id} - Active Consumers: {active_count}/{NUM_CONSUMERS}")
                _logger.critical(f"Reconnecting in {current_backoff}s...")
                time.sleep(current_backoff)
                self._channel = None
                self._connection = None
                current_backoff = min(current_backoff * 2, max_backoff)

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
        """Run the batch processor as background service with multiple consumers."""
        with self._lock:
            # Check if consumer is already running
            if self._consumer_active:
                _logger.warning("Consumer is already active, skipping startup")
                with self._consumers_lock:
                    _logger.warning(f"Current Active Consumers: {BatchProcessor._active_consumers}/{NUM_CONSUMERS}")
                return
            
            self._consumer_active = True
        
        try:
            _logger.info(f"\n{'='*80}")
            _logger.info(f"🔧 BATCH PROCESSOR SERVICE INITIALIZATION")
            _logger.info(f"{'='*80}")
            _logger.info(f"Target Consumers: {NUM_CONSUMERS}")
            _logger.info(f"Thread Pool Workers per Consumer: {THREAD_POOL_SIZE}")
            _logger.info(f"Prefetch Count: {PREFETCH_COUNT} messages")
            _logger.info(f"Max Retries: {MAX_RETRIES}")
            _logger.info(f"{'='*80}\n")
            
            # Start multiple consumers in DAEMON threads (so they don't block Odoo shutdown)
            consumer_threads = []
            for i in range(NUM_CONSUMERS):
                consumer_thread = Thread(
                    target=self.fetch_and_process_messages,
                    daemon=True,  # IMPORTANT: Daemon threads exit when main process exits
                    name=f"RabbitMQConsumer-{i+1}"
                )
                consumer_thread.start()
                consumer_threads.append(consumer_thread)
                _logger.info(f"[{i+1}/{NUM_CONSUMERS}] Spawned {consumer_thread.name}")
            
            _logger.info(f"\n{'='*80}")
            _logger.info(f"✓ BATCH PROCESSOR SERVICE STARTED")
            _logger.info(f"✓ All {NUM_CONSUMERS} consumers are spawning...")
            _logger.info(f"✓ Consumers will connect to queues and enter listening mode")
            _logger.info(f"✓ Check logs for consumer READY status confirmation")
            _logger.info(f"{'='*80}\n")
            
        except Exception as e:
            _logger.error(f"✗ Failed to start batch processor: {e}")
            with self._lock:
                self._consumer_active = False
            raise
