from __future__ import annotations

from celery import Celery

from testmind.core.config import Settings
from testmind.workers.tasks import register_tasks

DEFAULT_QUEUE = "testmind"


def create_celery_app(settings: Settings) -> Celery:
    time_limit = settings.celery_task_time_limit_seconds
    app = Celery("testmind")
    app.conf.update(
        broker_url=settings.redis_url.get_secret_value(),
        broker_connection_retry_on_startup=True,
        # Redis re-delivers unacknowledged messages after this long; it must exceed the longest
        # task, otherwise a slow task would be executed twice.
        broker_transport_options={"visibility_timeout": time_limit + 300},
        # JSON only: never unpickle data coming from the broker.
        task_serializer="json",
        accept_content=["json"],
        # Run state lives in PostgreSQL; Celery results are not needed.
        task_ignore_result=True,
        task_default_queue=DEFAULT_QUEUE,
        # Acknowledge after completion so a crashed worker's job is re-delivered.
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_time_limit=time_limit,
        task_soft_time_limit=max(time_limit - 30, 1),
        worker_hijack_root_logger=False,
        enable_utc=True,
        timezone="UTC",
    )
    register_tasks(app, settings)
    return app
