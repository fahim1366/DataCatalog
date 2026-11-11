from __future__ import annotations

import logging
from typing import List
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.api.dependencies.rbac import UserContext, get_current_user
from backend.src.core.database import get_db_session
from backend.src.models.catalog import CatalogTable
from backend.src.schemas.versioning import TableVersionItem, VersionDiffResponse
from backend.src.services.catalog.version_service import version_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Versioning"])


@router.get(
    "/tables/{table_id}/versions",
    response_model=List[TableVersionItem],
    summary="List all historical snapshot versions for a table",
)
async def list_table_versions(
    table_id: uuid.UUID,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> List[TableVersionItem]:
    """
    Returns the complete chronological history of immutable snapshot versions for a table.
    """
    stmt = select(CatalogTable).where(CatalogTable.id == table_id)
    result = await db.execute(stmt)
    table = result.scalar_one_or_none()
    if not table:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Table {table_id} not found",
        )

    versions = await version_service.get_versions(db, table_id)
    return [
        TableVersionItem(
            id=v.id,
            table_id=v.table_id,
            version_num=v.version_num,
            created_by_username=v.creator.username if v.creator else None,
            created_at=v.created_at,
        )
        for v in versions
    ]


@router.get(
    "/tables/{table_id}/versions/compare",
    response_model=VersionDiffResponse,
    summary="Compare two schema snapshot versions side-by-side",
)
async def compare_table_versions(
    table_id: uuid.UUID,
    v1: int = Query(..., ge=1, description="First version number to compare"),
    v2: int = Query(..., ge=1, description="Second version number to compare"),
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> VersionDiffResponse:
    """
    Computes a side-by-side structural delta between two versions of a table,
    identifying added columns, removed columns, and modified column attributes.
    """
    try:
        diff = await version_service.compare_versions(
            session=db,
            table_id=table_id,
            v1_num=v1,
            v2_num=v2,
        )
        return VersionDiffResponse(**diff)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

