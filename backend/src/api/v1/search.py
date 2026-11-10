from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.api.dependencies.rbac import UserContext, get_current_user
from backend.src.core.database import get_db_session
from backend.src.schemas.catalog import SearchResponse
from backend.src.services.catalog.search_service import search_service

router = APIRouter(tags=["Search"])


@router.get(
    "/search",
    response_model=SearchResponse,
    summary="Keyset-paginated full-text and trigram search across catalog tables and columns",
)
async def search_catalog(
    q: str = Query(..., min_length=1, description="Search query string"),
    limit: int = Query(50, ge=1, le=100, description="Max results per page"),
    cursor: Optional[str] = Query(None, description="Base64-encoded keyset cursor"),
    include_deleted: bool = Query(False, description="Include soft-deleted tables"),
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> SearchResponse:
    """
    Executes high-performance full-text GIN and trigram similarity search,
    strictly pushed down to PostgreSQL with user RBAC subqueries.
    """
    items, page_info = await search_service.search(
        session=db,
        user_ctx=user_ctx,
        query=q,
        limit=limit,
        cursor=cursor,
        include_deleted=include_deleted,
    )
    return SearchResponse(items=items, page_info=page_info)

