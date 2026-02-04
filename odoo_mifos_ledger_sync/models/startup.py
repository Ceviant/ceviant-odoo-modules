# -*- coding: utf-8 -*-
"""
Startup module to initialize RabbitMQ consumer on Odoo startup.
This ensures the consumer is started whenever Odoo starts.
"""
import logging
from odoo import api, SUPERUSER_ID
from odoo.tools import config

_logger = logging.getLogger(__name__)


def _start_rabbitmq_consumer(cr, registry):
    """Start RabbitMQ consumer on Odoo startup."""
    try:
        _logger.info("\n" + "="*80)
        _logger.info(">>> ODOO STARTUP: Initializing RabbitMQ Consumer")
        _logger.info("="*80 + "\n")
        
        # Get the environment
        env = api.Environment(cr, SUPERUSER_ID, {})
        
        # Get the batch processor model
        batch_processor_model = env['custom_journal_entry.batch_processor']
        
        # Start the consumer
        _logger.info("Calling run_batch_processor()...")
        batch_processor_model.run_batch_processor()
        
        _logger.info("✓ RabbitMQ consumer started successfully on Odoo startup")
        
    except Exception as e:
        _logger.error(f"✗ Failed to start RabbitMQ consumer on startup: {e}")
        import traceback
        _logger.error(traceback.format_exc())


# Register the startup hook
def post_load():
    """Called after Odoo modules are loaded."""
    # This will be called in __manifest__.py via post_load
    pass
