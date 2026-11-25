from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.api.dependencies.rbac import (
    UserContext,
    require_permission,
)
from backend.src.api.v1.connections import sanitize_uri_credentials
from backend.src.core.database import get_db_session
from backend.src.models.connections import DatabaseConnection
from backend.src.models.rbac import PermissionAction
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


def _build_connector_config(conn: DatabaseConnection) -> Dict[str, Any]:
    crypto = EnvelopeEncryptionService()
    creds = crypto.decrypt_credentials(conn.encrypted_dek, conn.encrypted_credentials)
    config: Dict[str, Any] = {
        "host": conn.host,
        "port": conn.port,
        "database_name": conn.database_name,
        "custom_params": conn.custom_params or {},
        "username": creds.get("username", ""),
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
        safe_err = sanitize_uri_credentials(str(exc))
        logger.error("Failed to initialize connector for %s: %s", connection_id, safe_err)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"امکان برقراری ارتباط با درایور پایگاه داده وجود ندارد: {safe_err}",
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
    except asyncio.TimeoutError:
        logger.error("Timeout extracting database users for connection %s", connection_id)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="زمان انتظار برای استخراج کاربران از پایگاه داده به پایان رسید (Timeout). لطفاً از دسترس‌پذیری سرور پایگاه داده اطمینان حاصل کنید.",
        )
    except Exception as exc:
        raw_err = str(exc).strip() or type(exc).__name__
        safe_err = sanitize_uri_credentials(raw_err)
        logger.error("Error extracting database users for connection %s: %s", connection_id, safe_err)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"خطا در استخراج کاربران پایگاه داده: {safe_err}",
        )
