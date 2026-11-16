from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.src.api.dependencies.rbac import UserContext, get_current_user
from backend.src.core.database import get_db_session
from backend.src.models.catalog import CatalogTable
from backend.src.schemas.drift import (
    ColumnDriftDetail,
    DescriptionDriftDetailResponse,
    DescriptionDriftResolutionResponse,
    ResolveDescriptionDriftRequest,
)
from backend.src.services.ingestion.drift_service import drift_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tables", tags=["Governance"])


@router.get(
    "/{table_id}/drift",
    response_model=DescriptionDriftDetailResponse,
    summary="Inspect table description drift details",
)
async def get_table_drift_details(
    table_id: uuid.UUID,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> DescriptionDriftDetailResponse:
    """
    Returns side-by-side comparison between native source database comment and
    curated approved description for a table and its columns.
    """
    stmt = (
        select(CatalogTable)
        .options(selectinload(CatalogTable.columns))
        .where(CatalogTable.id == table_id)
    )
    res = await db.execute(stmt)
    table = res.scalar_one_or_none()
    if table is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Table with ID '{table_id}' not found.",
        )

    cols = sorted(table.columns or [], key=lambda c: c.ordinal_position)
    column_drifts = [
        ColumnDriftDetail(
            column_id=col.id,
            column_name=col.name,
            source_description=col.source_description,
            approved_description=col.approved_description,
            drift_detected=col.description_drift_detected,
        )
        for col in cols
    ]

    return DescriptionDriftDetailResponse(
        table_id=table.id,
        table_name=table.name,
        table_source_description=table.source_description,
        table_approved_description=table.approved_description,
        drift_detected=table.description_drift_detected,
        column_drifts=column_drifts,
    )


@router.post(
    "/{table_id}/drift/resolve",
    response_model=DescriptionDriftResolutionResponse,
    summary="Resolve table description drift",
)
async def resolve_table_drift(
    table_id: uuid.UUID,
    payload: ResolveDescriptionDriftRequest,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> DescriptionDriftResolutionResponse:
    """
    Resolves description drift on a table and optionally on columns by choosing an action:
    RETAIN_APPROVED, OVERWRITE_WITH_SOURCE, or CUSTOM_MERGE. Resets drift flag.
    """
    stmt = select(CatalogTable).where(CatalogTable.id == table_id)
    res = await db.execute(stmt)
    table = res.scalar_one_or_none()
    if table is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Table with ID '{table_id}' not found.",
        )

    try:
        await drift_service.resolve_table_drift(
            session=db,
            table_id=table_id,
            resolution_action=payload.resolution_action,
            custom_text=payload.custom_description,
            actor_id=user_ctx.user_id,
            actor_username=user_ctx.user.username,
        )

        for col_res in payload.column_resolutions or []:
            await drift_service.resolve_column_drift(
                session=db,
                column_id=col_res.column_id,
                resolution_action=col_res.resolution_action,
                custom_text=col_res.custom_description,
                actor_id=user_ctx.user_id,
                actor_username=user_ctx.user.username,
            )

        await db.commit()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    return DescriptionDriftResolutionResponse(
        success=True,
        message="Description drift resolved successfully.",
        resolved_at=datetime.now(timezone.utc),
    )
