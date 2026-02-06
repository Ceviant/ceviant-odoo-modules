import os
import pika
import json
import logging
from odoo import models, api
from .journal_utils import process_transaction
import time

logging.basicConfig(level=logging.DEBUG)

MAX_RETRIES = 5
RETRY_DELAY = 5

class BatchProcessor(models.Model):
    _name = 'custom_journal_entry.batch_processor'

    def process_message(self, ch, method, properties, body, retry_count=0):
        """Process a single message from RabbitMQ and route it to the appropriate handler."""
        batch_ref = None
        queue_type = None
        try:
            message = json.loads(body)
            batch_ref = message.get('batch_ref')
            payload = message.get('payload')
            queue_type = method.routing_key
            logging.info(f"Processing batch {batch_ref} from {queue_type}")

            if queue_type == 'odoo_transaction_queue':
                result = process_transaction(payload)
                if result.get('status') == 'success':
                    logging.info(f"Batch {batch_ref} success")
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                else:
                    logging.error(f"Batch {batch_ref} error: {result.get('message')}")
                    self.retry_or_move_to_failure_queue(ch, method, body, retry_count, queue_type)
            
            else:
                logging.error(f"Batch {batch_ref} unknown queue {queue_type}")
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        
        except Exception as e:
            logging.error(f"Batch {batch_ref} exception: {str(e)}", exc_info=True)
            if queue_type:
                self.retry_or_move_to_failure_queue(ch, method, body, retry_count, queue_type)
            else:
                ch.basic_ack(delivery_tag=method.delivery_tag)

    def retry_or_move_to_failure_queue(self, ch, method, body, retry_count, queue_type):
        """Retry message or move to failure queue."""
        batch_ref = "unknown"
        try:
            batch_ref = json.loads(body).get('batch_ref', 'unknown')
        except:
            pass
        
        if retry_count < MAX_RETRIES:
            logging.info(f"Batch {batch_ref} retry {retry_count + 1}/{MAX_RETRIES}")
            time.sleep(RETRY_DELAY)
            # Requeue the message back to the original queue
            ch.basic_publish(exchange='', routing_key=queue_type, body=body)
            ch.basic_ack(delivery_tag=method.delivery_tag)
        else:
            failure_queue = 'odoo_transaction_failure_queue'
            logging.error(f"Batch {batch_ref} moved to DLQ: {failure_queue}")
            ch.basic_publish(exchange='', routing_key=failure_queue, body=body)
            ch.basic_ack(delivery_tag=method.delivery_tag)

    def fetch_and_process_messages(self):
        """Continuously listen for messages from RabbitMQ."""
        host = os.getenv("RABBITMQ_HOST")
        port = os.getenv("RABBITMQ_PORT")
        virtual_host = os.getenv("RABBITMQ_VHOST")
        username = os.getenv("RABBITMQ_USERNAME")
        password = os.getenv("RABBITMQ_PASSWORD")

        connection = None
        channel = None
        try:
            connection_parameters = pika.ConnectionParameters(
                host=host, port=int(port), virtual_host=virtual_host,
                credentials=pika.PlainCredentials(username, password)
            )
            connection = pika.BlockingConnection(connection_parameters)
            channel = connection.channel()
            channel.queue_declare(queue='odoo_transaction_queue', durable=True)
            channel.queue_declare(queue='odoo_transaction_failure_queue', durable=True)

            # Set QoS to process one message at a time
            channel.basic_qos(prefetch_count=1)
            
            # Set up continuous consumer
            channel.basic_consume(
                queue='odoo_transaction_queue',
                on_message_callback=self.process_message,
                auto_ack=False
            )
            
            logging.info("Consumer started, waiting for messages...")
            channel.start_consuming()
        
        except Exception as e:
            logging.error(f"RabbitMQ error: {str(e)}")
        finally:
            if channel:
                channel.close()
            if connection:
                connection.close()

    @api.model
    def run_batch_processor(self):
        """Run the batch processor as a cron job."""
        self.fetch_and_process_messages()
