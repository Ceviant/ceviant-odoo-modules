# -*- coding: utf-8 -*-
import logging

from . import models
from . import controllers

_logger = logging.getLogger(__name__)


def post_load_hook():
    """Start the optional RabbitMQ consumer when the server starts."""
    from .models.batch_processor import ENABLE_ENV, consumer_enabled
    if not consumer_enabled():
        _logger.info("Ledger sync RabbitMQ consumer disabled (set %s=1 to enable)", ENABLE_ENV)
        return

    from odoo import SUPERUSER_ID, api
    from odoo.modules.registry import Registry
    from odoo.tools import config

    for dbname in (config.get('db_name') or '').split(','):
        dbname = dbname.strip()
        if not dbname:
            continue
        try:
            with Registry(dbname).cursor() as cr:
                api.Environment(cr, SUPERUSER_ID, {})['custom_journal_entry.batch_processor'].run_batch_processor()
        except Exception:  # noqa: BLE001 - never block server start-up
            _logger.exception("Could not start ledger sync consumer for %s", dbname)
