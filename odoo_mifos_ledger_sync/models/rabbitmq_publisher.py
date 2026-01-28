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
    host = os.getenv("RABBITMQ_HOST")
    port = os.getenv("RABBITMQ_PORT")
    virtual_host = os.getenv("RABBITMQ_VHOST")
    username = os.getenv("RABBITMQ_USERNAME")
    password = os.getenv("RABBITMQ_PASSWORD")

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
    while retries < MAX_PUBLISH_RETRIES:
        try:
            connection_parameters = get_rabbitmq_connection()
            connection = pika.BlockingConnection(connection_parameters)
            channel = connection.channel()

            channel.queue_declare(queue=queue_name, durable=True)

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

            logging.info(f"Message published to {queue_name} with batch_ref {batch_ref}")
            connection.close()
            return batch_ref

        except pika.exceptions.AMQPError as e:
            retries += 1
            logging.error(f"Publish attempt {retries} failed: {e}")
            time.sleep(PUBLISH_RETRY_DELAY)

    logging.error(f"Failed to publish to RabbitMQ after {MAX_PUBLISH_RETRIES} attempts.")
    return None

def publish_account_entry_to_rabbitmq(payload):
    return publish_message_to_rabbitmq('account_queue', payload)

def publish_journal_entry_to_rabbitmq(payload):
    return publish_message_to_rabbitmq('transaction_queue', payload)

def publish_journal_entry_update_to_rabbitmq(payload):
    return publish_message_to_rabbitmq('update_journal_queue', payload)
