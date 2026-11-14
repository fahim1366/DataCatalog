from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.api.dependencies.rbac import UserContext, get_current_user
from backend.src.core.database import get_db_session
from backend.src.models.rbac import (
    EntityType,
    PermissionAction,
    PermissionEffect,
    PermissionRule,
)
from backend.src.schemas.audit import AuditLogResponse
from backend.src.services.audit.audit_query_service import audit_query_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Audit Logs"])


async def require_view_audit_logs_permission(
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> UserContext:
    """
    Enforces that the active user possesses VIEW_AUDIT_LOGS permission.
    Superadmins bypass this check.
    Deny rules take precedence over allow rules.
    """
    if user_ctx.is_superadmin:
        return user_ctx

    if not user_ctx.role_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: User lacks VIEW_AUDIT_LOGS permission.",
        )

    # 1. Check explicit DENY
    deny_stmt = select(1).where(
        PermissionRule.role_id.in_(user_ctx.role_ids),
        PermissionRule.action == PermissionAction.VIEW_AUDIT_LOGS,
        PermissionRule.effect == PermissionEffect.DENY,
    )
    deny_res = await db.execute(deny_stmt)
    if deny_res.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Access to audit logs is explicitly denied.",
        )

    # 2. Check ALLOW
    allow_stmt = select(1).where(
        PermissionRule.role_id.in_(user_ctx.role_ids),
        PermissionRule.action == PermissionAction.VIEW_AUDIT_LOGS,
        PermissionRule.effect == PermissionEffect.ALLOW,
    )
    allow_res = await db.execute(allow_stmt)
    if allow_res.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: User lacks VIEW_AUDIT_LOGS permission.",
        )

    return user_ctx


@router.get(
    "/audit-logs",
    response_model=AuditLogResponse,
    status_code=status.HTTP_200_OK,
    summary="Keyset-paginated immutable audit logs with multi-column filtering",
)
@router.get(
    "/audit/logs",
    response_model=AuditLogResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
@router.get(
    "/audit",
    response_model=AuditLogResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def list_audit_logs(
    entity_type: Optional[EntityType] = Query(
        None, description="Filter by entity type (DATABASE, SCHEMA, TABLE, COLUMN)"
    ),
    entity_id: Optional[uuid.UUID] = Query(None, description="Filter by entity ID"),
    actor_id: Optional[uuid.UUID] = Query(None, description="Filter by actor ID"),
    action: Optional[str] = Query(None, description="Filter by action name"),
    start_time: Optional[datetime] = Query(
        None, description="Filter by starting timestamp (inclusive)"
    ),
    end_time: Optional[datetime] = Query(
        None, description="Filter by ending timestamp (inclusive)"
    ),
    cursor: Optional[str] = Query(
        None, description="Base64-encoded keyset cursor for pagination"
    ),
    limit: int = Query(
        50,
        ge=1,
        le=200,
        description="Maximum number of audit log records to return",
    ),
    user_ctx: UserContext = Depends(require_view_audit_logs_permission),
    db: AsyncSession = Depends(get_db_session),
) -> AuditLogResponse:
    """
    Retrieves keyset-paginated immutable audit log records.
    Filters across actor, entity type, entity ID, action, and timestamp ranges
    with partition pruning and composite index acceleration.
    """
    return await audit_query_service.query_audit_logs(
        session=db,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_id=actor_id,
        action=action,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
        cursor=cursor,
    )

