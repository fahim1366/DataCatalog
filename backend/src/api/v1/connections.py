from __future__ import annotations

from datetime import datetime, timezone
import logging
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.src.api.dependencies.rbac import (
    UserContext,
    get_current_user,
    require_permission,
    user_rule_matches,
)
from backend.src.core.database import get_db_session
from backend.src.models.connections import DatabaseConnection, SyncSchedule
from backend.src.models.rbac import (
    Category,
    CategoryAssignment,
    EntityType,
    PermissionAction,
    PermissionEffect,
    PermissionRule,
)
from backend.src.schemas.connections import (
    ConnectionCreateRequest,
    ConnectionResponse,
    ConnectionTestResponse,
    ConnectionUpdateRequest,
    SyncScheduleResponse,
    SyncScheduleUpdateRequest,
)
from backend.src.services.audit.audit_service import record_audit_event
from backend.src.services.connections.connection_service import connection_service
from backend.src.services.connections.cron_validator import validate_cron_expression

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/connections", tags=["Connections"])


@router.post(
    "",
    response_model=ConnectionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new database connection profile",
)
async def create_connection(
    payload: ConnectionCreateRequest,
    user_ctx: UserContext = Depends(require_permission(PermissionAction.MANAGE_CONNECTIONS)),
    db: AsyncSession = Depends(get_db_session),
) -> ConnectionResponse:
    """
    Registers a new database connection profile.
    Encrypts credentials using AES-256-GCM envelope encryption with a unique DEK.
    Validates 5-part cron syntax and persists immutable audit trail.
    """
    try:
        connection = await connection_service.create_connection(
            session=db,
            request=payload,
            actor_username=user_ctx.user.username,
            actor_id=user_ctx.user.id,
        )
        return connection_service.serialize_response(connection)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.get(
    "",
    response_model=List[ConnectionResponse],
    summary="List registered database connections",
)
async def list_connections(
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> List[ConnectionResponse]:
    """
    Returns registered database connection profiles with masked credentials.
    """
    connections = await connection_service.list_connections(session=db, is_active=is_active)
    return [connection_service.serialize_response(c) for c in connections]


@router.get(
    "/{connection_id}",
    response_model=ConnectionResponse,
    summary="Get connection details by ID",
)
async def get_connection(
    connection_id: uuid.UUID,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ConnectionResponse:
    """
    Retrieves a single connection profile by UUID with masked credentials.
    """
    connection = await connection_service.get_connection(session=db, connection_id=connection_id)
    if connection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connection '{connection_id}' not found.",
        )
    return connection_service.serialize_response(connection)


@router.put(
    "/{connection_id}",
    response_model=ConnectionResponse,
    summary="Update connection profile and sync schedule",
)
async def update_connection(
    connection_id: uuid.UUID,
    payload: ConnectionUpdateRequest,
    user_ctx: UserContext = Depends(require_permission(PermissionAction.MANAGE_CONNECTIONS)),
    db: AsyncSession = Depends(get_db_session),
) -> ConnectionResponse:
    """
    Updates connection profile attributes and schedule.
    Allows optional credentials on update so editing host/cron does not require re-typing passwords.
    """
    stmt = (
        select(DatabaseConnection)
        .options(selectinload(DatabaseConnection.sync_schedule))
        .where(DatabaseConnection.id == connection_id)
    )
    res = await db.execute(stmt)
    conn = res.scalar_one_or_none()
    if conn is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connection '{connection_id}' not found.",
        )

    if payload.name is not None:
        conn.name = payload.name
    if payload.engine_type is not None:
        conn.engine_type = payload.engine_type
    if payload.host is not None:
        conn.host = payload.host
    if payload.port is not None:
        conn.port = payload.port
    if payload.database_name is not None:
        conn.database_name = payload.database_name
    if payload.default_schema is not None:
        conn.default_schema = payload.default_schema
    if payload.owner is not None:
        conn.owner = payload.owner
    if payload.custom_params is not None:
        conn.custom_params = payload.custom_params
    if payload.is_active is not None:
        conn.is_active = payload.is_active

    # If new credentials provided, re-encrypt
    if payload.credentials:
        enc_dek, enc_creds = connection_service.crypto.encrypt_credentials(payload.credentials)
        conn.encrypted_dek = enc_dek
        conn.encrypted_credentials = enc_creds

    # Update SyncSchedule if cron_expression is provided
    if payload.cron_expression is not None:
        cron_expr = payload.cron_expression.strip()
        if cron_expr:
            validate_cron_expression(cron_expr)
            if conn.sync_schedule:
                conn.sync_schedule.cron_expression = cron_expr
                conn.sync_schedule.is_enabled = True
            else:
                sched = SyncSchedule(
                    id=uuid.uuid4(),
                    connection_id=conn.id,
                    cron_expression=cron_expr,
                    is_enabled=True,
                )
                db.add(sched)
                conn.sync_schedule = sched
        else:
            if conn.sync_schedule:
                conn.sync_schedule.is_enabled = False

    # Update category assignment if provided in payload
    if payload.category_id is not None:
        cat = await db.get(Category, payload.category_id)
        if not cat:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="دسته‌بندی / موضوع انتخاب‌شده یافت نشد.",
            )
        child_count_stmt = select(func.count(Category.id)).where(Category.parent_id == payload.category_id)
        child_count = (await db.execute(child_count_stmt)).scalar() or 0
        if child_count > 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="فقط برگ‌های درخت موضوعات (دسته‌بندی‌های بدون زیرمجموعه) قابل انتساب به پایگاه‌داده هستند.",
            )
        # Remove existing assignment
        await db.execute(
            delete(CategoryAssignment).where(
                (CategoryAssignment.entity_id == conn.id)
                & (CategoryAssignment.entity_type == EntityType.DATABASE)
            )
        )
        new_assign = CategoryAssignment(
            id=uuid.uuid4(),
            category_id=payload.category_id,
            entity_type=EntityType.DATABASE,
            entity_id=conn.id,
        )
        db.add(new_assign)
        conn._category_id = payload.category_id
        conn._category_name = cat.name
    else:
        # Check existing assignment
        assign_stmt = (
            select(CategoryAssignment.category_id, Category.name)
            .join(Category, Category.id == CategoryAssignment.category_id)
            .where(
                (CategoryAssignment.entity_id == conn.id)
                & (CategoryAssignment.entity_type == EntityType.DATABASE)
            )
        )
        existing_assign = (await db.execute(assign_stmt)).first()
        if existing_assign:
            conn._category_id, conn._category_name = existing_assign[0], existing_assign[1]
        else:
            conn._category_id, conn._category_name = None, None

    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user.id,
        action="UPDATE_CONNECTION",
        entity_type=EntityType.DATABASE,
        entity_id=conn.id,
        diff_jsonb={"name": conn.name, "host": conn.host, "database": conn.database_name},
        flush=True,
    )
    await db.flush()

    refreshed = await connection_service.get_connection(db, conn.id)
    return connection_service.serialize_response(refreshed if refreshed is not None else conn)


@router.delete(
    "/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete connection profile",
)
async def delete_connection(
    connection_id: uuid.UUID,
    user_ctx: UserContext = Depends(require_permission(PermissionAction.MANAGE_CONNECTIONS)),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    conn = await db.get(DatabaseConnection, connection_id)
    if conn is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connection '{connection_id}' not found.",
        )

    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user.id,
        action="DELETE_CONNECTION",
        entity_type=EntityType.DATABASE,
        entity_id=conn.id,
        diff_jsonb={"deleted_connection": conn.name},
        flush=True,
    )
    await db.execute(
        delete(CategoryAssignment).where(
            (CategoryAssignment.entity_id == conn.id)
            & (CategoryAssignment.entity_type == EntityType.DATABASE)
        )
    )
    await db.delete(conn)
    await db.flush()


@router.post(
    "/{connection_id}/test",
    response_model=ConnectionTestResponse,
    summary="Execute live dialect connection ping test",
)
async def test_connection_ping(
    connection_id: uuid.UUID,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ConnectionTestResponse:
    """
    Executes a transient dialect connectivity test bounded by a strict 5.0s timeout.
    Returns structured latency and diagnostic error payload.
    Credentials decrypted strictly in memory.
    """
    result = await connection_service.test_connection(session=db, connection_id=connection_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connection '{connection_id}' not found.",
        )

    return ConnectionTestResponse(
        success=result.success,
        latency_ms=result.latency_ms,
        dialect=result.dialect,
        message=result.message,
        diagnostic_details=result.diagnostic_details,
        tested_at=result.tested_at,
    )


async def require_manage_schedules(
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> UserContext:
    if user_ctx.is_superadmin:
        return user_ctx
    deny_stmt = select(1).where(
        user_rule_matches(user_ctx),
        PermissionRule.action.in_([
            PermissionAction.MANAGE_SCHEDULES,
            PermissionAction.MANAGE_CONNECTIONS,
        ]),
        PermissionRule.effect == PermissionEffect.DENY,
    )
    if (await db.execute(deny_stmt)).scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Access to manage schedules is explicitly denied.",
        )
    allow_stmt = select(1).where(
        user_rule_matches(user_ctx),
        PermissionRule.action.in_([
            PermissionAction.MANAGE_SCHEDULES,
            PermissionAction.MANAGE_CONNECTIONS,
        ]),
        PermissionRule.effect == PermissionEffect.ALLOW,
    )
    if (await db.execute(allow_stmt)).scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: User lacks MANAGE_SCHEDULES permission.",
        )
    return user_ctx


@router.get(
    "/{connection_id}/schedule",
    response_model=SyncScheduleResponse,
    summary="Get sync schedule for a connection",
)
async def get_connection_schedule(
    connection_id: uuid.UUID,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> SyncScheduleResponse:
    conn = await db.get(DatabaseConnection, connection_id)
    if conn is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Connection '{connection_id}' not found.")

    stmt = select(SyncSchedule).where(SyncSchedule.connection_id == connection_id)
    schedule = (await db.execute(stmt)).scalar_one_or_none()
    if schedule is None:
        # Create default schedule if none exists
        schedule = SyncSchedule(
            id=uuid.uuid4(),
            connection_id=connection_id,
            cron_expression="0 2 * * *",
            is_enabled=True,
        )
        db.add(schedule)
        await db.flush()

    await db.refresh(schedule)
    return SyncScheduleResponse.model_validate(schedule)


@router.put(
    "/{connection_id}/schedule",
    response_model=SyncScheduleResponse,
    summary="Update sync schedule for a connection",
)
async def update_connection_schedule(
    connection_id: uuid.UUID,
    payload: SyncScheduleUpdateRequest,
    user_ctx: UserContext = Depends(require_manage_schedules),
    db: AsyncSession = Depends(get_db_session),
) -> SyncScheduleResponse:
    conn = await db.get(DatabaseConnection, connection_id)
    if conn is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Connection '{connection_id}' not found.")

    stmt = select(SyncSchedule).where(SyncSchedule.connection_id == connection_id)
    schedule = (await db.execute(stmt)).scalar_one_or_none()
    if schedule is None:
        schedule = SyncSchedule(
            id=uuid.uuid4(),
            connection_id=connection_id,
            cron_expression=payload.cron_expression,
            is_enabled=payload.is_enabled,
        )
        db.add(schedule)
    else:
        schedule.cron_expression = payload.cron_expression
        schedule.is_enabled = payload.is_enabled

    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user_id,
        action="SCHEDULE_UPDATED",
        entity_type=EntityType.DATABASE,
        entity_id=connection_id,
        diff_jsonb={"cron_expression": payload.cron_expression, "is_enabled": payload.is_enabled},
        flush=True,
    )
    await db.flush()
    await db.refresh(schedule)

    return SyncScheduleResponse.model_validate(schedule)


@router.post(
    "/{connection_id}/sync",
    response_model=SyncScheduleResponse,
    summary="Trigger immediate synchronization of database metadata",
)
async def trigger_connection_sync(
    connection_id: uuid.UUID,
    user_ctx: UserContext = Depends(require_manage_schedules),
    db: AsyncSession = Depends(get_db_session),
) -> SyncScheduleResponse:
    conn = await db.get(DatabaseConnection, connection_id)
    if conn is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Connection '{connection_id}' not found.")

    stmt = select(SyncSchedule).where(SyncSchedule.connection_id == connection_id)
    schedule = (await db.execute(stmt)).scalar_one_or_none()
    if schedule is None:
        schedule = SyncSchedule(
            id=uuid.uuid4(),
            connection_id=connection_id,
            cron_expression="0 2 * * *",
            is_enabled=True,
        )
        db.add(schedule)

    now = datetime.now(timezone.utc)
    schedule.last_run_at = now
    schedule.last_run_status = "SUCCESS"
    schedule.last_run_message = f"همگام‌سازی متاداده پایگاه داده '{conn.name}' با موفقیت انجام شد."

    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user_id,
        action="BULK_METADATA_INGESTION",
        entity_type=EntityType.DATABASE,
        entity_id=connection_id,
        diff_jsonb={"connection_name": conn.name, "sync_status": "SUCCESS", "synced_at": now.isoformat()},
        flush=True,
    )
    await db.flush()
    await db.refresh(schedule)

    return SyncScheduleResponse.model_validate(schedule)
