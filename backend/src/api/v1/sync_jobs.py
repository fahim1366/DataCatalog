from __future__ import annotations

import asyncio
import base64
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.src.api.dependencies.rbac import UserContext, get_current_user
from backend.src.core.celery_app import (
    QUEUE_PRIORITY,
    celery_app,
    has_active_celery_worker,
    is_broker_available,
)
from backend.src.core.database import get_db_session
from backend.src.models.connections import DatabaseConnection, SyncSchedule
from backend.src.models.rbac import EntityType
from backend.src.models.sync_jobs import SyncJob, SyncJobStatus, SyncTriggerType
from backend.src.schemas.sync_jobs import (
    BatchExpediteRequest,
    BatchExpediteResponse,
    SyncJobDetailResponse,
    SyncJobListResponse,
    SyncJobQueueItem,
    SyncJobResponse,
    SyncQueueStatusResponse,
)
from backend.src.services.audit.audit_service import record_audit_event
from backend.src.services.workers.tasks import execute_metadata_sync, manual_metadata_sync_task

logger = logging.getLogger(__name__)

# In-memory tracking of jobs being auto-recovered to prevent duplicate concurrent triggers
_recovering_job_ids: set[uuid.UUID] = set()

router = APIRouter(prefix="/sync-jobs", tags=["Synchronization"])


def encode_sync_cursor(created_at: datetime, item_id: uuid.UUID) -> str:
    payload = {
        "created_at": created_at.isoformat(),
        "id": str(item_id),
    }
    raw = json.dumps(payload).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8")


def decode_sync_cursor(cursor_str: str) -> Tuple[datetime, uuid.UUID]:
    raw = base64.urlsafe_b64decode(cursor_str.encode("utf-8")).decode("utf-8")
    payload = json.loads(raw)
    return datetime.fromisoformat(payload["created_at"]), uuid.UUID(payload["id"])


from backend.src.api.v1.connections import require_manage_schedules


@router.get(
    "",
    response_model=SyncJobListResponse,
    summary="Global Sync Job Monitor",
)
async def list_sync_jobs(
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by status enum"),
    connection_id: Optional[uuid.UUID] = Query(None, description="Filter by connection ID"),
    cursor: Optional[str] = Query(None, description="Keyset cursor token"),
    limit: int = Query(50, ge=1, le=100, description="Items limit"),
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> SyncJobListResponse:
    """
    Returns global execution history across all databases, filterable by status,
    connection_id, and paginated with deterministic keyset cursor.
    """
    base_where = []
    if status_filter:
        base_where.append(SyncJob.status == status_filter)
    if connection_id:
        base_where.append(SyncJob.connection_id == connection_id)

    # Total count matching filters
    count_stmt = select(func.count(SyncJob.id))
    if base_where:
        count_stmt = count_stmt.where(*base_where)
    total_res = await db.execute(count_stmt)
    total = total_res.scalar() or 0

    query = select(SyncJob).order_by(SyncJob.created_at.desc(), SyncJob.id.desc())
    if base_where:
        query = query.where(*base_where)

    if cursor:
        try:
            cursor_dt, cursor_id = decode_sync_cursor(cursor)
            query = query.where(
                or_(
                    SyncJob.created_at < cursor_dt,
                    (SyncJob.created_at == cursor_dt) & (SyncJob.id < cursor_id),
                )
            )
        except Exception:
            logger.warning("Invalid cursor token provided: %s", cursor)

    # Fetch limit + 1 to detect next page
    query = query.limit(limit + 1)
    res = await db.execute(query)
    rows = list(res.scalars().all())

    next_cursor = None
    if len(rows) > limit:
        next_item = rows[limit - 1]
        next_cursor = encode_sync_cursor(next_item.created_at, next_item.id)
        rows = rows[:limit]

    items = [SyncJobResponse.model_validate(row) for row in rows]
    return SyncJobListResponse(items=items, total=total, next_cursor=next_cursor)


@router.get(
    "/queue",
    response_model=SyncQueueStatusResponse,
    summary="Get live sync queue and running execution state",
)
async def get_sync_queue(
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> SyncQueueStatusResponse:
    """
    Returns live synchronization queue and active running jobs ordered by priority DESC, created_at ASC.
    Calculates queue_position and estimated_wait_seconds based on recent historical average job duration.
    """
    worker_online = has_active_celery_worker()

    # Calculate average duration of recent SUCCESS sync jobs (default 15s)
    avg_stmt = select(func.avg(SyncJob.duration_ms)).where(
        SyncJob.status == SyncJobStatus.SUCCESS.value,
        SyncJob.duration_ms.is_not(None),
        SyncJob.duration_ms > 0,
    )
    avg_res = await db.execute(avg_stmt)
    avg_duration_ms = avg_res.scalar()
    avg_seconds = max(1, round(float(avg_duration_ms) / 1000.0)) if avg_duration_ms and avg_duration_ms > 0 else 15

    # Query running jobs
    running_stmt = (
        select(SyncJob)
        .options(selectinload(SyncJob.connection))
        .where(SyncJob.status == SyncJobStatus.RUNNING.value)
        .order_by(SyncJob.started_at.desc(), SyncJob.created_at.desc())
    )
    running_res = await db.execute(running_stmt)
    running_rows = list(running_res.scalars().all())
    running_jobs = [
        SyncJobQueueItem(
            **SyncJobResponse.model_validate(job).model_dump(),
            connection_name=job.connection.name if job.connection else None,
            queue_position=None,
            estimated_wait_seconds=None,
        )
        for job in running_rows
    ]

    # Query queued jobs ordered by priority DESC, created_at ASC
    queued_stmt = (
        select(SyncJob)
        .options(selectinload(SyncJob.connection))
        .where(SyncJob.status == SyncJobStatus.QUEUED.value)
        .order_by(SyncJob.priority.desc(), SyncJob.created_at.asc())
    )
    queued_res = await db.execute(queued_stmt)
    queued_rows = list(queued_res.scalars().all())

    # Auto-recover stale jobs waiting more than 10s if worker_online is False
    now = datetime.now(timezone.utc)
    if not worker_online:
        for job in queued_rows:
            job_created_at = job.created_at if job.created_at.tzinfo else job.created_at.replace(tzinfo=timezone.utc)
            waiting_seconds = (now - job_created_at).total_seconds()
            if waiting_seconds > 10 and job.id not in _recovering_job_ids:
                _recovering_job_ids.add(job.id)
                logger.info(
                    "Auto-recovering stale QUEUED sync job %s (waiting %.1fs without online worker) via in-process task",
                    job.id,
                    waiting_seconds,
                )
                task = asyncio.create_task(
                    execute_metadata_sync(
                        connection_id=job.connection_id,
                        job_id=job.id,
                    )
                )
                task.add_done_callback(lambda _t, jid=job.id: _recovering_job_ids.discard(jid))

    queued_jobs = [
        SyncJobQueueItem(
            **SyncJobResponse.model_validate(job).model_dump(),
            connection_name=job.connection.name if job.connection else None,
            queue_position=idx,
            estimated_wait_seconds=idx * avg_seconds,
        )
        for idx, job in enumerate(queued_rows, start=1)
    ]

    return SyncQueueStatusResponse(
        worker_online=worker_online,
        running_jobs=running_jobs,
        queued_jobs=queued_jobs,
    )


@router.post(
    "/batch-expedite",
    response_model=BatchExpediteResponse,
    status_code=status.HTTP_200_OK,
    summary="Batch expedite synchronization for multiple connections out-of-order",
)
async def batch_expedite_sync(
    payload: BatchExpediteRequest,
    user_ctx: UserContext = Depends(require_manage_schedules),
    db: AsyncSession = Depends(get_db_session),
) -> BatchExpediteResponse:
    """
    Accepts connection_ids to trigger immediate out-of-order synchronization for multiple selected databases at once.
    Promotes already-queued jobs to priority=10 or enqueues new ones.
    """
    dispatched_jobs = []
    worker_online = has_active_celery_worker()
    tasks_to_dispatch = []

    for conn_id in payload.connection_ids:
        # Check connection exists
        conn_stmt = select(DatabaseConnection).where(DatabaseConnection.id == conn_id)
        conn = (await db.execute(conn_stmt)).scalar_one_or_none()
        if not conn:
            continue

        # Check if already queued or running
        active_stmt = select(SyncJob).where(
            SyncJob.connection_id == conn_id,
            SyncJob.status.in_([SyncJobStatus.QUEUED.value, SyncJobStatus.RUNNING.value]),
        )
        active_job = (await db.execute(active_stmt)).scalar_one_or_none()

        if active_job:
            if active_job.status == SyncJobStatus.QUEUED.value:
                active_job.priority = 10
                active_job.triggered_by = SyncTriggerType.MANUAL.value
                await db.flush()

                tasks_to_dispatch.append((conn_id, active_job.id))

                await record_audit_event(
                    session=db,
                    actor_username=user_ctx.user.username,
                    actor_id=user_ctx.user.id,
                    action="EXPEDITE_SYNC_JOB",
                    entity_type=EntityType.DATABASE,
                    entity_id=conn_id,
                    diff_jsonb={"job_id": str(active_job.id), "priority": 10, "status": active_job.status},
                    flush=True,
                )
                dispatched_jobs.append(SyncJobResponse.model_validate(active_job))
            else:
                dispatched_jobs.append(SyncJobResponse.model_validate(active_job))
        else:
            sched_stmt = select(SyncSchedule).where(SyncSchedule.connection_id == conn_id)
            sched = (await db.execute(sched_stmt)).scalar_one_or_none()
            new_job = SyncJob(
                id=uuid.uuid4(),
                connection_id=conn_id,
                schedule_id=sched.id if sched else None,
                status=SyncJobStatus.QUEUED.value,
                triggered_by=SyncTriggerType.MANUAL.value,
                priority=10,
            )
            db.add(new_job)
            await db.flush()
            await db.refresh(new_job)

            tasks_to_dispatch.append((conn_id, new_job.id))

            await record_audit_event(
                session=db,
                actor_username=user_ctx.user.username,
                actor_id=user_ctx.user.id,
                action="EXPEDITE_SYNC_JOB",
                entity_type=EntityType.DATABASE,
                entity_id=conn_id,
                diff_jsonb={"job_id": str(new_job.id), "priority": 10, "status": SyncJobStatus.QUEUED.value},
                flush=True,
            )
            dispatched_jobs.append(SyncJobResponse.model_validate(new_job))

    # Commit all SyncJob changes BEFORE dispatching background tasks
    await db.commit()

    # Dispatch tasks now that DB rows are guaranteed visible to background workers
    for c_id, j_id in tasks_to_dispatch:
        if worker_online:
            try:
                manual_metadata_sync_task.apply_async(
                    args=[str(c_id), str(j_id)],
                    queue=QUEUE_PRIORITY,
                    priority=9,
                    retry=False,
                )
            except Exception:
                asyncio.create_task(
                    execute_metadata_sync(connection_id=c_id, job_id=j_id)
                )
        else:
            asyncio.create_task(
                execute_metadata_sync(connection_id=c_id, job_id=j_id)
            )

    return BatchExpediteResponse(
        jobs=dispatched_jobs,
        message=f"{len(dispatched_jobs)} پایگاه‌داده برای همگام‌سازی فوری خارج از نوبت ارسال شدند.",
        total_dispatched=len(dispatched_jobs),
    )


@router.get(
    "/{job_id}",
    response_model=SyncJobDetailResponse,
    summary="Get detailed sync job diagnostics",
)
async def get_sync_job_detail(
    job_id: uuid.UUID,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> SyncJobDetailResponse:
    """
    Retrieves full sync job details including schema-level partial failures,
    error traces, tables/columns counts, and duration breakdown.
    """
    stmt = select(SyncJob).options(selectinload(SyncJob.connection)).where(SyncJob.id == job_id)
    res = await db.execute(stmt)
    job = res.scalar_one_or_none()
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SyncJob '{job_id}' not found.",
        )

    conn_name = job.connection.name if job.connection else None

    # Construct SyncJobDetailResponse
    resp = SyncJobDetailResponse(
        id=job.id,
        connection_id=job.connection_id,
        schedule_id=job.schedule_id,
        status=job.status,
        triggered_by=job.triggered_by,
        started_at=job.started_at,
        completed_at=job.completed_at,
        duration_ms=job.duration_ms,
        tables_scanned=job.tables_scanned,
        tables_created=job.tables_created,
        tables_updated=job.tables_updated,
        tables_deleted=job.tables_deleted,
        columns_scanned=job.columns_scanned,
        changes_detected=job.changes_detected,
        error_message=job.error_message,
        created_at=job.created_at,
        connection_name=conn_name,
        error_trace_jsonb=job.error_trace_jsonb or {},
    )
    return resp


@router.post(
    "/{job_id}/retry",
    response_model=SyncJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Retry a failed sync job",
)
async def retry_sync_job(
    job_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    user_ctx: UserContext = Depends(require_manage_schedules),
    db: AsyncSession = Depends(get_db_session),
) -> SyncJobResponse:
    """
    Re-enqueues a failed or partially failed sync job with RETRY trigger.
    """
    stmt = select(SyncJob).where(SyncJob.id == job_id)
    res = await db.execute(stmt)
    old_job = res.scalar_one_or_none()
    if old_job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SyncJob '{job_id}' not found.",
        )

    retry_job = SyncJob(
        id=uuid.uuid4(),
        connection_id=old_job.connection_id,
        schedule_id=old_job.schedule_id,
        status=SyncJobStatus.QUEUED.value,
        triggered_by=SyncTriggerType.RETRY.value,
        priority=10,
    )
    db.add(retry_job)
    await db.flush()

    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user.id,
        action="RETRY_SYNC_JOB",
        entity_type=EntityType.DATABASE,
        entity_id=old_job.connection_id,
        diff_jsonb={"original_job_id": str(job_id), "retry_job_id": str(retry_job.id)},
        flush=True,
    )
    # Commit transaction BEFORE dispatching so the retry SyncJob row is 100% committed
    await db.commit()
    await db.refresh(retry_job)

    if has_active_celery_worker():
        try:
            manual_metadata_sync_task.apply_async(
                args=[str(retry_job.connection_id), str(retry_job.id)],
                queue=QUEUE_PRIORITY,
                retry=False,
            )
        except Exception as exc:
            logger.warning(
                "Could not dispatch retry Celery task (%s). Falling back to in-process execution via asyncio.create_task.",
                exc,
            )
            asyncio.create_task(
                execute_metadata_sync(
                    connection_id=retry_job.connection_id,
                    job_id=retry_job.id,
                )
            )
    else:
        logger.info(
            "Celery worker not online. Executing retry sync in-process via asyncio.create_task for connection %s.",
            retry_job.connection_id,
        )
        asyncio.create_task(
            execute_metadata_sync(
                connection_id=retry_job.connection_id,
                job_id=retry_job.id,
            )
        )

    return SyncJobResponse.model_validate(retry_job)


@router.post(
    "/{job_id}/expedite",
    response_model=SyncJobResponse,
    status_code=status.HTTP_200_OK,
    summary="Expedite a queued sync job out of order",
)
async def expedite_sync_job(
    job_id: uuid.UUID,
    user_ctx: UserContext = Depends(require_manage_schedules),
    db: AsyncSession = Depends(get_db_session),
) -> SyncJobResponse:
    """
    Promotes a QUEUED job to priority = 10 (triggered_by = MANUAL), dispatches it immediately
    (or executes directly via asyncio.create_task if no worker is active),
    emits an EXPEDITE_SYNC_JOB AuditLog event, and returns the updated SyncJobResponse.
    """
    stmt = select(SyncJob).where(SyncJob.id == job_id)
    res = await db.execute(stmt)
    job = res.scalar_one_or_none()
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SyncJob '{job_id}' not found.",
        )
    if job.status != SyncJobStatus.QUEUED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"تنها کارهای در صف انتظار (QUEUED) امکان اجرای فوری دارند. وضعیت فعلی: {job.status}",
        )

    job.priority = 10
    job.triggered_by = SyncTriggerType.MANUAL.value
    await db.flush()

    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user.id,
        action="EXPEDITE_SYNC_JOB",
        entity_type=EntityType.DATABASE,
        entity_id=job.connection_id,
        diff_jsonb={"job_id": str(job.id), "priority": 10, "status": job.status},
        flush=True,
    )
    # Commit transaction BEFORE dispatching so the priority update is 100% committed
    await db.commit()
    await db.refresh(job)

    if has_active_celery_worker():
        try:
            manual_metadata_sync_task.apply_async(
                args=[str(job.connection_id), str(job.id)],
                queue=QUEUE_PRIORITY,
                priority=9,
                retry=False,
            )
        except Exception as exc:
            logger.warning(
                "Could not dispatch Celery task (%s). Falling back to in-process execution via asyncio.create_task.",
                exc,
            )
            asyncio.create_task(
                execute_metadata_sync(
                    connection_id=job.connection_id,
                    job_id=job.id,
                )
            )
    else:
        logger.info(
            "Celery worker not online. Executing expedited sync in-process via asyncio.create_task for connection %s.",
            job.connection_id,
        )
        asyncio.create_task(
            execute_metadata_sync(
                connection_id=job.connection_id,
                job_id=job.id,
            )
        )

    return SyncJobResponse.model_validate(job)


@router.post(
    "/{job_id}/cancel",
    response_model=SyncJobResponse,
    status_code=status.HTTP_200_OK,
    summary="Cancel a queued or running sync job",
)
async def cancel_sync_job(
    job_id: uuid.UUID,
    user_ctx: UserContext = Depends(require_manage_schedules),
    db: AsyncSession = Depends(get_db_session),
) -> SyncJobResponse:
    """
    Cancels a QUEUED (or stuck) job by setting status = 'CANCELLED',
    completed_at = now(), error_message = 'لغو شده توسط کاربر', revoking any Celery task if present,
    emitting a CANCEL_SYNC_JOB AuditLog event, and releasing the concurrency lock so new syncs can run.
    """
    stmt = select(SyncJob).where(SyncJob.id == job_id)
    res = await db.execute(stmt)
    job = res.scalar_one_or_none()
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SyncJob '{job_id}' not found.",
        )
    if job.status in [
        SyncJobStatus.SUCCESS.value,
        SyncJobStatus.FAILED.value,
        SyncJobStatus.PARTIAL_FAILURE.value,
        SyncJobStatus.CANCELLED.value,
    ]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"این کار همگام‌سازی قبلاً به پایان رسیده است یا لغو شده است (وضعیت: {job.status}).",
        )

    now = datetime.now(timezone.utc)
    job.status = SyncJobStatus.CANCELLED.value
    job.completed_at = now
    job.error_message = "لغو شده توسط کاربر"
    if job.started_at:
        job.duration_ms = int((now - job.started_at).total_seconds() * 1000)

    try:
        celery_app.control.revoke(str(job.id), terminate=True)
    except Exception as exc:
        logger.debug("Failed or skipped Celery task revoke for job %s: %s", job.id, exc)

    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user.id,
        action="CANCEL_SYNC_JOB",
        entity_type=EntityType.DATABASE,
        entity_id=job.connection_id,
        diff_jsonb={"job_id": str(job.id), "status": SyncJobStatus.CANCELLED.value},
        flush=True,
    )
    await db.commit()
    await db.refresh(job)

    return SyncJobResponse.model_validate(job)

