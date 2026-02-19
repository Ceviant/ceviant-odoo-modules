# -*- coding: utf-8 -*-
import logging
from . import models
from . import controllers
from . import views
from . import data

_logger = logging.getLogger(__name__)


def post_load_hook():
    """Start RabbitMQ consumer to drain queue backlog"""
    try:
        from odoo import api, SUPERUSER_ID
        from odoo.tools import config
        
        _logger.info("\n" + "="*80)
        _logger.info(">>> ODOO POST-LOAD: Starting RabbitMQ Consumer (Queue Drainer)")
        _logger.info("="*80 + "\n")
        
        db = config.get('db_name')
        if db:
            from odoo.api import Environment
            from odoo.sql_db import db_connect
            
            cr = db_connect(db).cursor()
            try:
                env = api.Environment(cr, SUPERUSER_ID, {})
                batch_processor = env['custom_journal_entry.batch_processor']
                batch_processor.run_batch_processor()
                _logger.info("✓ RabbitMQ consumer started on post_load")
            except Exception as inner_e:
                _logger.error(f"✗ Error starting batch processor: {inner_e}")
                import traceback
                _logger.error(traceback.format_exc())
            finally:
                try:
                    cr.close()
                except Exception as close_e:
                    _logger.warning(f"Warning closing cursor: {close_e}")
    except Exception as e:
        _logger.error(f"✗ Error in post_load_hook: {e}")
        import traceback
        _logger.error(traceback.format_exc())
