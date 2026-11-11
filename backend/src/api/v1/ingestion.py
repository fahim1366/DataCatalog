from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.api.dependencies.rbac import UserContext, get_current_user
from backend.src.core.database import get_db_session
from backend.src.schemas.ingestion import (
    BulkIngestionRequest,
    BulkIngestionResponse,
)
from backend.src.services.ingestion.bulk_ingestion_service import (
    bulk_ingestion_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ingestion", tags=["Ingestion"])


@router.post(
    "/bulk",
    response_model=BulkIngestionResponse,
    status_code=status.HTTP_200_OK,
    summary="Bulk metadata ingestion with batch upsert and automatic versioning",
)
async def bulk_ingest_metadata(
    payload: BulkIngestionRequest,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> BulkIngestionResponse:
    """
    Ingests metadata batches for tables and columns using PostgreSQL batch upsert.
    Preserves human-curated approved descriptions, soft-deletes missing entities,
    and automatically triggers TableVersion snapshots and SchemaChangeLog entries.
    """
    try:
        response = await bulk_ingestion_service.ingest_bulk_metadata(
            session=db,
            payload=payload,
            actor_id=user_ctx.user.id,
            actor_username=user_ctx.user.username,
        )
        return response
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

