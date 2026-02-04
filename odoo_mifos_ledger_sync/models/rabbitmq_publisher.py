import pika
import json
import random
import string
import logging
import os
import time

logging.basicConfig(level=logging.DEBUG)

MAX_PUBLISH_RETRIES = 3
PUBLISH_RETRY_DELAY = 5  # seconds

def generate_batch_reference(length=6):
    """Generate a unique alphanumeric batch reference starting with 'resp-' followed by 6 random digits."""
    characters = string.digits
    random_digits = ''.join(random.choice(characters) for _ in range(length))
    return f'resp-{random_digits}'

def get_rabbitmq_connection():
    host = os.getenv("RABBITMQ_HOST", "rabbitmq")
    port = os.getenv("RABBITMQ_PORT", "5672")
    virtual_host = os.getenv("RABBITMQ_VHOST", "/")
    username = os.getenv("RABBITMQ_USERNAME", "admin")
    password = os.getenv("RABBITMQ_PASSWORD", "admin")

    try:
        return pika.ConnectionParameters(
            host=host, port=int(port), virtual_host=virtual_host,
            credentials=pika.PlainCredentials(username, password)
        )
    except ValueError as e:
        logging.error(f"Invalid configuration: {e}")
        raise

def publish_message_to_rabbitmq(queue_name, payload):
    retries = 0
    logging.info(f"=== RABBITMQ PUBLISH START ===")
    logging.info(f"Queue Name: {queue_name}")
    logging.info(f"Payload: {json.dumps(payload, indent=2)}")
    
    while retries < MAX_PUBLISH_RETRIES:
        try:
            logging.info(f"Attempting to connect to RabbitMQ (attempt {retries + 1}/{MAX_PUBLISH_RETRIES})")
            connection_parameters = get_rabbitmq_connection()
            logging.info(f"Connection Parameters - Host: {connection_parameters.host}, Port: {connection_parameters.port}, VHost: {connection_parameters.virtual_host}")
            
            connection = pika.BlockingConnection(connection_parameters)
            logging.info(f"Connected to RabbitMQ successfully")
            channel = connection.channel()
            logging.info(f"Channel created")

            channel.queue_declare(queue=queue_name, durable=True)
            logging.info(f"Queue '{queue_name}' declared")

            batch_ref = generate_batch_reference()
            message = {
                'batch_ref': batch_ref,
                'payload': payload
            }

            channel.basic_publish(
                exchange='',
                routing_key=queue_name,
                body=json.dumps(message),
                properties=pika.BasicProperties(delivery_mode=2)
            )

            logging.info(f"✓ Message published to queue '{queue_name}' with batch_ref '{batch_ref}'")
            logging.info(f"=== RABBITMQ PUBLISH SUCCESS ===")
            connection.close()
            return batch_ref

        except pika.exceptions.AMQPError as e:
            retries += 1
            logging.error(f"✗ Publish attempt {retries} failed: {type(e).__name__} - {e}")
            logging.error(f"Retrying in {PUBLISH_RETRY_DELAY} seconds...")
            time.sleep(PUBLISH_RETRY_DELAY)
        except Exception as e:
            retries += 1
            logging.error(f"✗ Unexpected error on attempt {retries}: {type(e).__name__} - {e}")
            logging.error(f"Retrying in {PUBLISH_RETRY_DELAY} seconds...")
            time.sleep(PUBLISH_RETRY_DELAY)

    logging.error(f"✗ FAILED to publish to RabbitMQ after {MAX_PUBLISH_RETRIES} attempts.")
    logging.error(f"=== RABBITMQ PUBLISH FAILED ===")
    return None

def publish_account_entry_to_rabbitmq(payload):
    return publish_message_to_rabbitmq('odoo_account_queue', payload)

def publish_journal_entry_to_rabbitmq(payload):
    return publish_message_to_rabbitmq('odoo_transaction_queue', payload)

def publish_journal_entry_update_to_rabbitmq(payload):
    return publish_message_to_rabbitmq('odoo_update_journal_queue', payload)
