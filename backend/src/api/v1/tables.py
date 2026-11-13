from __future__ import annotations

from typing import Optional
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.api.dependencies.rbac import UserContext, get_current_user
from backend.src.core.database import get_db_session
from backend.src.models.catalog import (
    CatalogColumn,
    CatalogTable,
    LifecycleStatus,
)
from backend.src.models.rbac import (
    EntityType,
    PermissionAction,
    PermissionEffect,
)
from backend.src.models.versioning import SchemaChangeLog, SchemaChangeType
from backend.src.schemas.catalog import (
    ColumnItem,
    DescriptionUpdateRequest,
    TableDetail,
    TableListResponse,
)
from backend.src.services.audit.audit_service import record_audit_event
from backend.src.services.catalog.search_service import search_service
from backend.src.services.rbac.category_service import CategoryService

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
    raw_table = await db.get(CatalogTable, table_id)
    if not raw_table or raw_table.status == LifecycleStatus.DELETED:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Table {table_id} not found",
        )

    detail = await search_service.get_table_details(
        session=db,
        table_id=table_id,
        user_ctx=user_ctx,
    )
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied to table {table_id}",
        )
    return TableDetail(**detail)


@router.patch(
    "/tables/{table_id}/description",
    response_model=TableDetail,
    summary="Inline edit of table approved description",
)
async def update_table_description(
    table_id: uuid.UUID,
    payload: DescriptionUpdateRequest,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> TableDetail:
    """
    Updates the approved description of a catalog table.
    Enforces EDIT_DESCRIPTION RBAC permission, logs SCHEMA_CHANGE_LOG, and writes immutable AuditLog.
    """
    table = await db.get(CatalogTable, table_id)
    if not table or table.status == LifecycleStatus.DELETED:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Table {table_id} not found",
        )

    if not user_ctx.is_superadmin:
        effect = await CategoryService.resolve_effective_permission(
            session=db,
            user_ctx=user_ctx,
            entity_type=EntityType.TABLE,
            entity_id=table.id,
            action=PermissionAction.EDIT_DESCRIPTION,
        )
        if effect != PermissionEffect.ALLOW:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Forbidden: User lacks EDIT_DESCRIPTION permission for table {table_id}",
            )

    old_desc = table.approved_description
    new_desc = payload.approved_description
    table.approved_description = new_desc

    # Insert SchemaChangeLog
    change_log = SchemaChangeLog(
        id=uuid.uuid4(),
        table_id=table.id,
        change_type=SchemaChangeType.DESCRIPTION_UPDATED,
        column_name=None,
        old_value={"approved_description": old_desc},
        new_value={"approved_description": new_desc},
        actor_id=user_ctx.user_id,
    )
    db.add(change_log)

    # Insert AuditLog
    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user_id,
        action="EDIT_DESCRIPTION",
        entity_type=EntityType.TABLE,
        entity_id=table.id,
        diff_jsonb={
            "field": "approved_description",
            "old_value": old_desc,
            "new_value": new_desc,
        },
        flush=True,
    )
    await db.flush()

    detail = await search_service.get_table_details(
        session=db,
        table_id=table.id,
        user_ctx=user_ctx,
    )
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Table {table_id} could not be retrieved after update",
        )
    return TableDetail(**detail)


@router.patch(
    "/columns/{column_id}/description",
    response_model=ColumnItem,
    summary="Inline edit of column approved description",
)
async def update_column_description(
    column_id: uuid.UUID,
    payload: DescriptionUpdateRequest,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ColumnItem:
    """
    Updates the approved description of a catalog column.
    Enforces EDIT_DESCRIPTION RBAC permission, records SCHEMA_CHANGE_LOG, and writes immutable AuditLog.
    """
    column = await db.get(CatalogColumn, column_id)
    if not column or column.status == LifecycleStatus.DELETED:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Column {column_id} not found",
        )

    if not user_ctx.is_superadmin:
        effect = await CategoryService.resolve_effective_permission(
            session=db,
            user_ctx=user_ctx,
            entity_type=EntityType.TABLE,
            entity_id=column.table_id,
            action=PermissionAction.EDIT_DESCRIPTION,
        )
        if effect != PermissionEffect.ALLOW:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Forbidden: User lacks EDIT_DESCRIPTION permission for table {column.table_id}",
            )

    old_desc = column.approved_description
    new_desc = payload.approved_description
    column.approved_description = new_desc

    # Insert SchemaChangeLog
    change_log = SchemaChangeLog(
        id=uuid.uuid4(),
        table_id=column.table_id,
        change_type=SchemaChangeType.DESCRIPTION_UPDATED,
        column_name=column.name,
        old_value={"approved_description": old_desc},
        new_value={"approved_description": new_desc},
        actor_id=user_ctx.user_id,
    )
    db.add(change_log)

    # Insert AuditLog
    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user_id,
        action="EDIT_DESCRIPTION",
        entity_type=EntityType.COLUMN,
        entity_id=column.id,
        diff_jsonb={
            "field": "approved_description",
            "table_id": str(column.table_id),
            "column_name": column.name,
            "old_value": old_desc,
            "new_value": new_desc,
        },
        flush=True,
    )
    await db.flush()
    await db.refresh(column)

    return ColumnItem(
        id=column.id,
        table_id=column.table_id,
        name=column.name,
        ordinal_position=column.ordinal_position,
        data_type=column.data_type,
        raw_data_type=column.raw_data_type,
        is_nullable=column.is_nullable,
        is_primary_key=column.is_primary_key,
        is_foreign_key=column.is_foreign_key,
        default_value=column.default_value,
        source_description=column.source_description,
        approved_description=column.approved_description,
        effective_description=column.effective_description,
        classification_level=column.classification_level.value,
        status=column.status.value,
    )
