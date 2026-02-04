# -*- coding: utf-8 -*-
"""
Startup module to initialize DB Processor on Odoo startup.
This processes pending journal entries directly from the database instead of using RabbitMQ queue.
"""
import logging
from odoo import api, SUPERUSER_ID
from odoo.tools import config

_logger = logging.getLogger(__name__)


def _start_db_processor(cr, registry):
    """Start DB Processor on Odoo startup."""
    try:
        _logger.info("\n" + "="*80)
        _logger.info(">>> ODOO STARTUP: Initializing DB Processor")
        _logger.info("="*80 + "\n")
        
        # Get the environment
        env = api.Environment(cr, SUPERUSER_ID, {})
        
        # Get the DB processor model
        db_processor_model = env['db.processor']
        
        # Start the processor
        _logger.info("Calling start_processor()...")
        db_processor_model.start_processor()
        
        _logger.info("✓ DB Processor started successfully on Odoo startup")
        
    except Exception as e:
        _logger.error(f"✗ Failed to start DB Processor on startup: {e}")
        import traceback
        _logger.error(traceback.format_exc())


# Register the startup hook
def post_load():
    """Called after Odoo modules are loaded."""
    # This will be called in __manifest__.py via post_load
    pass
