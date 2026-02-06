import os
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
        queue_type = None
        try:
            message = json.loads(body)
            batch_ref = message.get('batch_ref')
            payload = message.get('payload')
            queue_type = method.routing_key

            if queue_type == 'odoo_transaction_queue':
                logging.info(f"Processing transaction for batch {batch_ref} --")
                result = process_transaction(payload)
                if result.get('status') == 'success':
                    self.send_notification(f"Journal batch {batch_ref} processed and updated successfully.")
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                else:
                    # Log the error but don't fail the whole batch
                    logging.warning(f"Transaction processing returned error status for batch {batch_ref}: {result.get('message', 'Unknown error')}")
                    self.send_notification(f"Warning: Journal batch {batch_ref} processing completed with errors: {result.get('message', 'Unknown error')}")
                    ch.basic_ack(delivery_tag=method.delivery_tag)
            elif queue_type == 'odoo_account_queue':
                try:
                    result, error = create_account(payload)
                    if error or not result:
                        logging.warning(f"Account creation returned error for batch {batch_ref}: {error}")
                        self.send_notification(f"Warning: Account batch {batch_ref} failed: {error}")
                        ch.basic_ack(delivery_tag=method.delivery_tag)
                    else:
                        odoo_id = result.get('odoo_account_id') if isinstance(result, dict) else None
                        self.send_notification(f"Account batch {batch_ref} processed successfully with Odoo account ID {odoo_id}.")
                        ch.basic_ack(delivery_tag=method.delivery_tag)
                except Exception as create_error:
                    logging.error(f"Account creation exception for batch {batch_ref}: {str(create_error)}")
                    self.send_notification(f"Error: Account batch {batch_ref} failed: {str(create_error)}")
                    self.retry_or_move_to_failure_queue(ch, method, body, retry_count, queue_type)
            elif queue_type == 'odoo_update_journal_queue':
                logging.info(f"Updating journal entry for batch {batch_ref} --")
                result = update_journal_entry_in_database(payload)
                if result.get('status') == 'success':
                    self.send_notification(f"Journal entry batch {batch_ref} updated successfully.")
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                else:
                    # Log the error but don't fail the whole batch
                    logging.warning(f"Journal entry update returned error status for batch {batch_ref}: {result.get('message', 'Unknown error')}")
                    self.send_notification(f"Warning: Journal entry batch {batch_ref} update completed with errors: {result.get('message', 'Unknown error')}")
                    ch.basic_ack(delivery_tag=method.delivery_tag)
            else:
                logging.warning(f"Unknown queue type {queue_type} for batch {batch_ref}")
                ch.basic_ack(delivery_tag=method.delivery_tag)
        except json.JSONDecodeError as e:
            batch_ref = batch_ref or "unknown"
            self.send_notification(f"JSON decode error processing batch {batch_ref}: {str(e)}")
            logging.error(f"JSON decode error: {str(e)}")
            if queue_type:
                self.retry_or_move_to_failure_queue(ch, method, body, retry_count, queue_type)
            else:
                # Can't determine queue type from malformed JSON, just acknowledge to prevent infinite loop
                ch.basic_ack(delivery_tag=method.delivery_tag)
        except pika.exceptions.AMQPChannelError as e:
            batch_ref = batch_ref or "unknown"
            self.send_notification(f"AMQP error processing batch {batch_ref}: {str(e)}")
            logging.error(f"AMQP error: {str(e)}")
            if queue_type:
                self.retry_or_move_to_failure_queue(ch, method, body, retry_count, queue_type)
            else:
                ch.basic_ack(delivery_tag=method.delivery_tag)
        except Exception as e:
            batch_ref = batch_ref or "unknown"
            self.send_notification(f"Unexpected error processing batch {batch_ref}: {str(e)}")
            logging.error(f"Unexpected error: {str(e)}")
            import traceback
            logging.error(f"Traceback: {traceback.format_exc()}")
            if queue_type:
                self.retry_or_move_to_failure_queue(ch, method, body, retry_count, queue_type)
            else:
                # Can't retry without queue type, just acknowledge
                ch.basic_ack(delivery_tag=method.delivery_tag)

    def retry_or_move_to_failure_queue(self, ch, method, body, retry_count, queue_type):
        """Retry message or move to failure queue. Prevents app shutdown on repeated failures."""
        try:
            batch_ref = "unknown"
            try:
                batch_ref = json.loads(body).get('batch_ref', 'unknown')
            except:
                pass
            
            if retry_count < MAX_RETRIES:
                logging.info(f"Retrying batch {batch_ref} ({retry_count+1}/{MAX_RETRIES})...")
                time.sleep(RETRY_DELAY)
                # Reprocess the message with incremented retry count
                self.process_message(ch, method, None, body, retry_count+1)
            else:
                failure_queue = {
                    'odoo_transaction_queue': 'odoo_transaction_failure_queue',
                    'odoo_account_queue': 'odoo_account_failure_queue',
                    'odoo_update_journal_queue': 'odoo_update_journal_failure_queue'
                }.get(queue_type)
                if failure_queue:
                    logging.error(f"Max retries reached for batch {batch_ref}. Moving to {failure_queue}.")
                    try:
                        ch.basic_publish(
                            exchange='',
                            routing_key=failure_queue,
                            body=body,
                            properties=pika.BasicProperties()
                        )
                    except Exception as pub_error:
                        logging.error(f"Failed to publish to failure queue: {str(pub_error)}")
                    # Always acknowledge to prevent message stuck in queue
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                else:
                    logging.error(f"No failure queue mapped for routing key '{queue_type}'. Acknowledging message anyway.")
                    # Acknowledge even without failure queue to prevent infinite loop
                    ch.basic_ack(delivery_tag=method.delivery_tag)
        except Exception as e:
            logging.error(f"Error in retry logic: {str(e)}")
            # Try to acknowledge the message to prevent it being reprocessed
            try:
                ch.basic_ack(delivery_tag=method.delivery_tag)
            except:
                pass

    def fetch_and_process_messages(self):
        """Fetch messages from RabbitMQ (both queues) and process them gracefully."""
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
            channel.queue_declare(queue='odoo_account_queue', durable=True)
            channel.queue_declare(queue='odoo_update_journal_queue', durable=True)
            channel.queue_declare(queue='odoo_transaction_failure_queue', durable=True)
            channel.queue_declare(queue='odoo_account_failure_queue', durable=True)
            channel.queue_declare(queue='odoo_update_journal_failure_queue', durable=True)

            for queue_name in ['odoo_transaction_queue', 'odoo_account_queue', 'odoo_update_journal_queue']:
                try:
                    method_frame, header_frame, body = channel.basic_get(queue=queue_name)
                    while method_frame:
                        try:
                            self.process_message(channel, method_frame, None, body)
                        except Exception as e:
                            logging.error(f"Error processing message from {queue_name}: {str(e)}")
                            # Continue with next message instead of crashing
                            try:
                                channel.basic_nack(delivery_tag=method_frame.delivery_tag, requeue=True)
                            except:
                                pass
                        method_frame, header_frame, body = channel.basic_get(queue=queue_name)
                except Exception as e:
                    logging.error(f"Error processing queue {queue_name}: {str(e)}")
                    # Continue with next queue
                    continue

        except pika.exceptions.AMQPConnectionError as e:
            logging.error(f"Failed to connect to RabbitMQ: {str(e)}")
            self.send_notification(f"RabbitMQ connection error: {str(e)}")
        except Exception as e:
            logging.error(f"Error in fetch_and_process_messages: {str(e)}")
            import traceback
            logging.error(f"Traceback: {traceback.format_exc()}")
            self.send_notification(f"Error processing messages: {str(e)}")
        finally:
            try:
                if channel and not channel.is_closed:
                    channel.close()
            except:
                pass
            try:
                if connection and connection.is_open:
                    connection.close()
            except:
                pass

    @api.model
    def run_batch_processor(self):
        """Run the batch processor as a cron job. Handles errors gracefully."""
        try:
            self.fetch_and_process_messages()
        except Exception as e:
            logging.error(f"Critical error in batch processor: {str(e)}")
            import traceback
            logging.error(f"Traceback: {traceback.format_exc()}")
            # Log the error but don't raise to prevent cron job from stopping
