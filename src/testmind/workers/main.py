"""Celery entry point: `celery -A testmind.workers.main worker`."""

from __future__ import annotations

from typing import Any

from celery.signals import setup_logging

from testmind.core.config import get_settings
from testmind.core.logging import configure_logging
from testmind.workers.celery_app import create_celery_app

settings = get_settings()
app = create_celery_app(settings)


@setup_logging.connect
def _configure_worker_logging(**_: Any) -> None:
    # Connecting to this signal stops Celery from installing its own log handlers.
    configure_logging(settings.log_level, json=settings.log_json)
