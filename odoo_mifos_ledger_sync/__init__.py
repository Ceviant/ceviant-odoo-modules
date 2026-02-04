# -*- coding: utf-8 -*-
import logging
from . import models
from . import controllers
from . import views
from . import data

_logger = logging.getLogger(__name__)

# No need for post_load hook - direct synchronous processing
