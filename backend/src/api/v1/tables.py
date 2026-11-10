from __future__ import annotations

from typing import Optional
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.api.dependencies.rbac import UserContext, get_current_user
from backend.src.core.database import get_db_session
from backend.src.schemas.catalog import TableDetail, TableListResponse
from backend.src.services.catalog.search_service import search_service

router = APIRouter(tags=["Tables"])


@router.get(
    "/schemas/{schema_id}/tables",
    response_model=TableListResponse,
    summary="Keyset-paginated tables for a schema",
)
async def list_schema_tables(
    schema_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200, description="Max results per page"),
    cursor: Optional[str] = Query(None, description="Base64-encoded keyset cursor"),
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> TableListResponse:
    """
    Returns keyset-paginated tables for a given schema, filtered by SQL-pushed RBAC.
    """
    items, page_info = await search_service.list_tables_by_schema(
        session=db,
        schema_id=schema_id,
        user_ctx=user_ctx,
        limit=limit,
        cursor=cursor,
    )
    return TableListResponse(items=items, page_info=page_info)


@router.get(
    "/tables/{table_id}",
    response_model=TableDetail,
    summary="Get table details with columns and active tags",
)
async def get_table(
    table_id: uuid.UUID,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> TableDetail:
    """
    Returns full metadata for a table, its columns, and tags, enforcing VIEW_METADATA RBAC.
    """
    detail = await search_service.get_table_details(
        session=db,
        table_id=table_id,
        user_ctx=user_ctx,
    )
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Table {table_id} not found or access denied",
        )
    return TableDetail(**detail)

