import pytest
from celery import Celery
from kombu import Queue

from backend.src.core.config import settings


def test_celery_configuration_and_queues():
    """T064: Unit tests asserting Celery configuration, broker, queues, and task limits."""
    from backend.src.core.celery_app import celery_app, QUEUE_PRIORITY, QUEUE_FAST, QUEUE_BULK

    # Celery instance
    assert isinstance(celery_app, Celery)
    assert celery_app.main == "enterprise_datacatalog"

    # Broker & backend URLs from settings
    assert celery_app.conf.broker_url == settings.CELERY_BROKER_URL
    assert celery_app.conf.result_backend == settings.CELERY_RESULT_BACKEND

    # Queue names and definitions
    assert QUEUE_PRIORITY == "metadata.priority"
    assert QUEUE_FAST == "metadata.fast"
    assert QUEUE_BULK == "metadata.bulk"

    queue_names = [q.name if isinstance(q, Queue) else q for q in celery_app.conf.task_queues]
    assert "metadata.priority" in queue_names
    assert "metadata.fast" in queue_names
    assert "metadata.bulk" in queue_names

    # Reliability and worker parameters
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.task_time_limit == 1800
    assert celery_app.conf.task_soft_time_limit == 1500
    assert celery_app.conf.task_serializer == "json"
    assert celery_app.conf.result_serializer == "json"
    assert "json" in celery_app.conf.accept_content
