from __future__ import annotations

import logging
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.api.dependencies.rbac import UserContext, get_current_user
from backend.src.core.database import get_db_session
from backend.src.schemas.connections import (
    ConnectionCreateRequest,
    ConnectionResponse,
    ConnectionTestResponse,
)
from backend.src.services.connections.connection_service import connection_service

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
    user_ctx: UserContext = Depends(get_current_user),
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

