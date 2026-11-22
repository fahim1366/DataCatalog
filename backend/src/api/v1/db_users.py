from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.api.dependencies.rbac import (
    UserContext,
    require_permission,
)
from backend.src.core.database import get_db_session
from backend.src.models.connections import DatabaseConnection
from backend.src.models.rbac import EntityType, PermissionAction
from backend.src.services.audit.audit_service import record_audit_event
from backend.src.services.connectors.registry import default_connector_registry
from backend.src.services.crypto.envelope import EnvelopeEncryptionService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/connections", tags=["Database Users"])


class DatabaseUserResponse(BaseModel):
    oid: str
    username: str
    can_login: bool
    is_superuser: bool
    can_create_db: bool
    can_create_role: bool
    connection_limit: int
    valid_until: Optional[str] = None
    member_of_roles: List[str] = Field(default_factory=list)
    active_sessions: int = 0


class DatabaseUserUpdateRequest(BaseModel):
    can_login: Optional[bool] = None
    connection_limit: Optional[int] = None
    valid_until: Optional[str] = None


class DatabaseUserActionResponse(BaseModel):
    success: bool
    message: str


def _build_connector_config(conn: DatabaseConnection) -> Dict[str, Any]:
    crypto = EnvelopeEncryptionService()
    creds = crypto.decrypt_credentials(conn.encrypted_dek, conn.encrypted_credentials)
    config: Dict[str, Any] = {
        "host": conn.host,
        "port": conn.port,
        "database_name": conn.database_name,
        "custom_params": conn.custom_params or {},
        "username": creds.get("username", "postgres"),
        "password": creds.get("password", ""),
    }
    del creds  # Ephemeral memory zeroing
    return config


@router.get(
    "/{connection_id}/db-users",
    response_model=List[DatabaseUserResponse],
    summary="Fetch live database users and their full attributes/roles/sessions",
)
async def get_database_users(
    connection_id: uuid.UUID,
    user_ctx: UserContext = Depends(require_permission(PermissionAction.MANAGE_DB_USERS)),
    db: AsyncSession = Depends(get_db_session),
) -> List[DatabaseUserResponse]:
    """
    Introspects target database catalog to list all users, roles, login states,
    assigned roles, and active sessions.
    Requires MANAGE_DB_USERS administrative permission.
    """
    conn = await db.get(DatabaseConnection, connection_id)
    if conn is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"پایگاه داده با شناسه '{connection_id}' یافت نشد.",
        )

    try:
        config = _build_connector_config(conn)
        connector = default_connector_registry.create_connector(conn.engine_type, config)
    except Exception as exc:
        logger.error("Failed to initialize connector for %s: %s", connection_id, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"امکان برقراری ارتباط با درایور پایگاه داده وجود ندارد: {str(exc)}",
        )

    if not hasattr(connector, "extract_database_users"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"قابلیت پایش کاربران برای موتور پایگاه داده '{conn.engine_type.value}' پیاده‌سازی نشده است.",
        )

    try:
        async with connector:
            users_raw = await connector.extract_database_users()
        return [DatabaseUserResponse(**u) for u in users_raw]
    except Exception as exc:
        logger.error("Error extracting database users for connection %s: %s", connection_id, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"خطا در استخراج کاربران پایگاه داده: {str(exc)}",
        )


@router.patch(
    "/{connection_id}/db-users/{username}",
    response_model=DatabaseUserActionResponse,
    summary="Update database user login permission, connection limit, or valid until",
)
async def update_database_user(
    connection_id: uuid.UUID,
    username: str,
    payload: DatabaseUserUpdateRequest,
    user_ctx: UserContext = Depends(require_permission(PermissionAction.MANAGE_DB_USERS)),
    db: AsyncSession = Depends(get_db_session),
) -> DatabaseUserActionResponse:
    """
    Modifies target database user attributes (can_login, connection_limit, valid_until)
    via ALTER ROLE, and records an immutable audit log entry.
    Requires MANAGE_DB_USERS permission.
    """
    conn = await db.get(DatabaseConnection, connection_id)
    if conn is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"پایگاه داده با شناسه '{connection_id}' یافت نشد.",
        )

    try:
        config = _build_connector_config(conn)
        connector = default_connector_registry.create_connector(conn.engine_type, config)
    except Exception as exc:
        logger.error("Failed to initialize connector for %s: %s", connection_id, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"امکان برقراری ارتباط با درایور پایگاه داده وجود ندارد: {str(exc)}",
        )

    if not hasattr(connector, "update_database_user"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"قابلیت مدیریت کاربران برای موتور پایگاه داده '{conn.engine_type.value}' پیاده‌سازی نشده است.",
        )

    try:
        async with connector:
            result = await connector.update_database_user(
                username=username,
                can_login=payload.can_login,
                connection_limit=payload.connection_limit,
                valid_until=payload.valid_until,
            )
    except Exception as exc:
        logger.error("Error executing update_database_user on %s: %s", username, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"خطا در اعمال تغییرات بر روی کاربر دیتابیس: {str(exc)}",
        )

    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("message", "خطا در تغییر مشخصات کاربر پایگاه داده"),
        )

    # Record immutable audit log
    changes_dict = {k: v for k, v in payload.model_dump().items() if v is not None}
    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user.id,
        action="UPDATE_DB_USER",
        entity_type=EntityType.DATABASE,
        entity_id=conn.id,
        diff_jsonb={
            "connection_id": str(conn.id),
            "connection_name": conn.name,
            "username": username,
            "changes": changes_dict,
        },
        flush=True,
    )
    await db.commit()

    return DatabaseUserActionResponse(
        success=True,
        message=result.get("message", f"کاربر «{username}» با موفقیت به‌روزرسانی شد."),
    )
