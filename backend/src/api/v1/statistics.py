from __future__ import annotations

import logging
from typing import Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.api.dependencies.rbac import UserContext, get_current_user
from backend.src.core.database import get_db_session
from backend.src.models.catalog import CatalogTable
from backend.src.models.statistics import TableStatisticsHistory
from backend.src.schemas.statistics import (
    TableStatisticPoint,
    TableStatisticsHistoryResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tables", tags=["Catalog"])


@router.get(
    "/{table_id}/statistics",
    response_model=TableStatisticsHistoryResponse,
    summary="Time-series statistical history for a table",
)
async def get_table_statistics(
    table_id: uuid.UUID,
    limit: int = Query(30, ge=1, le=100, description="Number of historical points"),
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> TableStatisticsHistoryResponse:
    """
    Returns time-series history of estimated row counts and size in bytes
    from table_statistics_history without structural version clutter.
    """
    table = await db.get(CatalogTable, table_id)
    if table is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Table with ID '{table_id}' not found.",
        )

    stmt = (
        select(TableStatisticsHistory)
        .where(TableStatisticsHistory.table_id == table_id)
        .order_by(TableStatisticsHistory.recorded_at.desc())
        .limit(limit)
    )
    res = await db.execute(stmt)
    records = list(res.scalars().all())

    data_points = [
        TableStatisticPoint(
            recorded_at=rec.recorded_at,
            row_count_estimate=rec.row_count_estimate,
            size_bytes=rec.size_bytes,
            sync_job_id=rec.sync_job_id,
        )
        for rec in records
    ]

    return TableStatisticsHistoryResponse(
        table_id=table_id,
        data_points=data_points,
    )
