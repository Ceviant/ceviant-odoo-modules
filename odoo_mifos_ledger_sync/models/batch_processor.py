import pika
import json
import logging
from odoo import models, api
from .journal_utils import process_transaction, update_journal_entry_in_database
from .account_utils import create_account
import time

logging.basicConfig(level=logging.DEBUG)

MAX_RETRIES = 5
RETRY_DELAY = 5

class BatchProcessor(models.Model):
    _name = 'custom_journal_entry.batch_processor'

    def send_notification(self, message):
        """Send notification about the transaction or account processing status."""
        logging.info(f"Notification: {message}")

    def process_message(self, ch, method, properties, body, retry_count=0):
        """Process a single message from RabbitMQ and route it to the appropriate handler."""
        batch_ref = None
        try:
            message = json.loads(body)
            batch_ref = message.get('batch_ref')
            payload = message.get('payload')
            queue_type = method.routing_key

            if queue_type == 'transaction_queue':
                logging.info(f"Processing transaction for batch {batch_ref} --")
                success = process_transaction(payload)
                if success:
                    self.send_notification(f"Journal batch {batch_ref} processed and updated successfully.")
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                else:
                    raise Exception("Transaction processing failed")
            elif queue_type == 'account_queue':
                success = create_account(payload)
                if success:
                    self.send_notification(f"Account batch {batch_ref} processed successfully.")
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                else:
                    raise Exception("Account creation failed")
            elif queue_type == 'update_journal_queue':
                logging.info(f"Updating journal entry for batch {batch_ref} --")
                success = update_journal_entry_in_database(payload)
                if success:
                    self.send_notification(f"Journal entry batch {batch_ref} updated successfully.")
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                else:
                    raise Exception("Journal entry update failed")
        except json.JSONDecodeError as e:
            self.send_notification(f"JSON decode error processing batch {batch_ref}: {str(e)}")
            logging.error(f"JSON decode error: {str(e)}")
            self.retry_or_move_to_failure_queue(ch, method, body, retry_count, queue_type)
        except pika.exceptions.AMQPChannelError as e:
            self.send_notification(f"AMQP error processing batch {batch_ref}: {str(e)}")
            logging.error(f"AMQP error: {str(e)}")
            self.retry_or_move_to_failure_queue(ch, method, body, retry_count, queue_type)
        except Exception as e:
            self.send_notification(f"Unexpected error processing batch {batch_ref}: {str(e)}")
            logging.error(f"Unexpected error: {str(e)}")
            self.retry_or_move_to_failure_queue(ch, method, body, retry_count, queue_type)

    def retry_or_move_to_failure_queue(self, ch, method, body, retry_count, queue_type):
        if retry_count < MAX_RETRIES:
            logging.info(f"Retrying batch {json.loads(body).get('batch_ref')} ({retry_count+1}/{MAX_RETRIES})...")
            time.sleep(RETRY_DELAY)
            self.process_message(ch, method, None, body, retry_count+1)
        else:
            failure_queue = {
                'transaction_queue': 'transaction_failure_queue',
                'account_queue': 'account_failure_queue',
                'update_journal_queue': 'update_journal_failure_queue'
            }.get(queue_type)
            if failure_queue:
                logging.error(f"Max retries reached for batch {json.loads(body).get('batch_ref')}. Moving to {failure_queue}.")
                ch.basic_publish(
                    exchange='',
                    routing_key=failure_queue,
                    body=body,
                    properties=pika.BasicProperties()
                )
                ch.basic_ack(delivery_tag=method.delivery_tag)
            else:
                logging.error(f"No failure queue mapped for routing key '{queue_type}'")

    def fetch_and_process_messages(self):
        """Fetch messages from RabbitMQ (both queues) and process them."""
        connection_parameters = pika.ConnectionParameters(
            host='rabbitmq',
            port=5672,
            virtual_host='/',
            credentials=pika.PlainCredentials('guest', 'guest')
        )
        connection = pika.BlockingConnection(connection_parameters)
        channel = connection.channel()
        channel.queue_declare(queue='transaction_queue', durable=True)
        channel.queue_declare(queue='account_queue', durable=True)
        channel.queue_declare(queue='update_journal_queue', durable=True)
        channel.queue_declare(queue='transaction_failure_queue', durable=True)
        channel.queue_declare(queue='account_failure_queue', durable=True)
        channel.queue_declare(queue='update_journal_failure_queue', durable=True)

        for queue_name in ['transaction_queue', 'account_queue', 'update_journal_queue']:
            method_frame, header_frame, body = channel.basic_get(queue=queue_name)
            while method_frame:
                self.process_message(channel, method_frame, None, body)
                method_frame, header_frame, body = channel.basic_get(queue=queue_name)

        connection.close()

    @api.model
    def run_batch_processor(self):
        """Run the batch processor as a cron job."""
        self.fetch_and_process_messages()
