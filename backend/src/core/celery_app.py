from __future__ import annotations

import logging
from celery import Celery
from kombu import Queue

from backend.src.core.config import settings

logger = logging.getLogger(__name__)

# Canonical queue names
QUEUE_PRIORITY = "metadata.priority"
QUEUE_FAST = "metadata.fast"
QUEUE_BULK = "metadata.bulk"

# Initialize Celery app instance
celery_app = Celery(
    "enterprise_datacatalog",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

# Queue topology definitions
task_queues = (
    Queue(QUEUE_PRIORITY, routing_key=QUEUE_PRIORITY),
    Queue(QUEUE_FAST, routing_key=QUEUE_FAST),
    Queue(QUEUE_BULK, routing_key=QUEUE_BULK),
)

# Task routing mappings
task_routes = {
    "backend.src.services.workers.tasks.execute_metadata_sync_task": {"queue": QUEUE_BULK},
    "backend.src.services.workers.tasks.test_connection_task": {"queue": QUEUE_FAST},
    "backend.src.services.workers.tasks.manual_metadata_sync_task": {"queue": QUEUE_PRIORITY},
}

celery_app.conf.update(
    task_queues=task_queues,
    task_default_queue=QUEUE_BULK,
    task_routes=task_routes,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_time_limit=1800,
    task_soft_time_limit=1500,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    result_expires=86400,
    broker_connection_timeout=1,
    broker_connection_retry=False,
)

# Auto-discover worker tasks
celery_app.autodiscover_tasks(["backend.src.services.workers"])
