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


def is_broker_available(broker_url: str = settings.CELERY_BROKER_URL) -> bool:
    """Checks whether the Celery broker host/port is reachable."""
    import socket
    from urllib.parse import urlparse

    try:
        parsed = urlparse(broker_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 6379
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except Exception:
        return False


def has_active_celery_worker(timeout: float = 0.3) -> bool:
    """
    Checks whether the Celery broker is reachable and at least one worker process is actively responding.
    Returns False quickly if Redis broker is down or no worker processes are active.
    """
    if not is_broker_available():
        return False
    try:
        inspect = celery_app.control.inspect(timeout=timeout)
        pings = inspect.ping() if inspect else None
        return bool(pings and len(pings) > 0)
    except Exception as exc:
        logger.debug("Active worker inspection failed: %s", exc)
        return False


