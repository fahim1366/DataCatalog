from __future__ import annotations

import base64
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.src.api.dependencies.rbac import UserContext, get_current_user
from backend.src.core.celery_app import QUEUE_PRIORITY
from backend.src.core.database import get_db_session
from backend.src.models.connections import DatabaseConnection
from backend.src.models.rbac import EntityType
from backend.src.models.sync_jobs import SyncJob, SyncJobStatus, SyncTriggerType
from backend.src.schemas.sync_jobs import (
    SyncJobDetailResponse,
    SyncJobListResponse,
    SyncJobResponse,
)
from backend.src.services.audit.audit_service import record_audit_event
from backend.src.services.workers.tasks import execute_metadata_sync, manual_metadata_sync_task

logger = logging.getLogger(__name__)

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
    )
    db.add(retry_job)
    await db.flush()
    await db.refresh(retry_job)

    try:
        manual_metadata_sync_task.apply_async(
            args=[str(retry_job.connection_id), str(retry_job.id)],
            queue=QUEUE_PRIORITY,
            retry=False,
        )
    except Exception as exc:
        logger.warning(
            "Could not dispatch retry Celery task (%s). Falling back to in-process execution via BackgroundTasks.",
            exc,
        )
        background_tasks.add_task(
            execute_metadata_sync,
            connection_id=retry_job.connection_id,
            job_id=retry_job.id,
        )

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
    await db.flush()

    return SyncJobResponse.model_validate(retry_job)
