from __future__ import annotations

import asyncio
import concurrent.futures
from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Dict, List, Optional
import uuid

from celery.beat import Scheduler, ScheduleEntry
from croniter import croniter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.celery_app import QUEUE_BULK, celery_app
from backend.src.core.database import get_session_context
from backend.src.models.connections import SyncSchedule
from backend.src.models.sync_jobs import SyncJob, SyncJobStatus
from backend.src.services.scheduler.jitter import calculate_schedule_jitter
from backend.src.services.workers.circuit_breaker import circuit_breaker_manager
from backend.src.services.workers.tasks import execute_metadata_sync_task

logger = logging.getLogger(__name__)


def _run_coroutine(coro):
    """Safely executes an async coroutine from synchronous Celery Beat loops."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(lambda: asyncio.run(coro)).result()
    else:
        return asyncio.run(coro)


class DatabaseScheduler(Scheduler):
    """
    Custom dynamic database-backed Celery Beat scheduler.

    Features:
    - Queries `sync_schedules` directly from PostgreSQL without restarting the scheduler.
    - Honors `is_enabled` and 3-state Circuit Breaker (suppressing OPEN circuits in cooldown).
    - Incorporates deterministic SHA-256 jitter to disperse schedule executions evenly.
    - Atomically creates `SyncJob` records with QUEUED status and enqueues tasks to `metadata.bulk`.
    """

    def __init__(self, *args, **kwargs):
        # Allow instantiating without Celery Beat arguments during testing
        if args or kwargs:
            super().__init__(*args, **kwargs)
        self._max_interval = 5.0

    @staticmethod
    def calculate_next_run(
        cron_expression: str,
        connection_id: uuid.UUID,
        base_time: datetime,
        max_jitter_seconds: int = 900,
    ) -> datetime:
        """
        Calculates the next execution datetime combining standard cron progression
        with deterministic SHA-256 jitter.
        """
        # Ensure UTC tzinfo
        if base_time.tzinfo is None:
            base_time = base_time.replace(tzinfo=timezone.utc)

        cron = croniter(cron_expression, base_time)
        base_next = cron.get_next(datetime)
        if base_next.tzinfo is None:
            base_next = base_next.replace(tzinfo=timezone.utc)

        jitter_seconds = calculate_schedule_jitter(connection_id, max_jitter_seconds=max_jitter_seconds)
        return base_next + timedelta(seconds=jitter_seconds)

    async def dispatch_due_schedules(
        self,
        session: Optional[AsyncSession] = None,
        current_time: Optional[datetime] = None,
        enqueue_tasks: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Scans all active schedules, evaluates circuit breaker status and due timestamps,
        dispatches queued SyncJobs, and updates next_run_at.
        """
        now = current_time or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        async def _dispatch(sess: AsyncSession) -> List[Dict[str, Any]]:
            stmt = select(SyncSchedule).where(SyncSchedule.is_enabled == True)
            res = await sess.execute(stmt)
            schedules = res.scalars().all()

            dispatched: List[Dict[str, Any]] = []

            for schedule in schedules:
                # 1. Circuit breaker gate
                if not circuit_breaker_manager.can_execute_sync(schedule, current_time=now):
                    logger.debug(
                        "Schedule %s skipped by circuit breaker (state: %s)",
                        schedule.id,
                        schedule.circuit_state,
                    )
                    continue

                # 2. Check if schedule has initialized next_run_at
                if schedule.next_run_at is None:
                    # Calculate next scheduled run
                    schedule.next_run_at = self.calculate_next_run(
                        schedule.cron_expression,
                        schedule.connection_id,
                        now,
                    )
                    continue

                # Ensure timezone awareness for comparison
                sched_next_run = schedule.next_run_at
                if sched_next_run.tzinfo is None:
                    sched_next_run = sched_next_run.replace(tzinfo=timezone.utc)

                # 3. Check if due
                if sched_next_run <= now:
                    job = SyncJob(
                        id=uuid.uuid4(),
                        connection_id=schedule.connection_id,
                        schedule_id=schedule.id,
                        status=SyncJobStatus.QUEUED.value,
                        triggered_by="SCHEDULE",
                    )
                    sess.add(job)

                    if enqueue_tasks:
                        execute_metadata_sync_task.apply_async(
                            args=[str(schedule.connection_id), str(job.id)],
                            queue=QUEUE_BULK,
                        )

                    schedule.last_run_at = now
                    schedule.next_run_at = self.calculate_next_run(
                        schedule.cron_expression,
                        schedule.connection_id,
                        now,
                    )

                    dispatched.append({
                        "schedule_id": str(schedule.id),
                        "connection_id": str(schedule.connection_id),
                        "job_id": str(job.id),
                        "next_run_at": schedule.next_run_at.isoformat(),
                    })
                    logger.info(
                        "Dispatched SyncJob %s for schedule %s (next run: %s)",
                        job.id,
                        schedule.id,
                        schedule.next_run_at,
                    )

            await sess.commit()
            return dispatched

        if session is not None:
            return await _dispatch(session)
        else:
            async with get_session_context() as sess:
                return await _dispatch(sess)

    def tick(self) -> float:
        """Celery Beat loop iteration hook."""
        try:
            _run_coroutine(self.dispatch_due_schedules(enqueue_tasks=True))
        except Exception as exc:
            logger.error("Error executing DatabaseScheduler tick: %s", exc, exc_info=True)
        return self._max_interval
