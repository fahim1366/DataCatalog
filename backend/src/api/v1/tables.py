from __future__ import annotations

from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, ConfigDict
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.src.api.dependencies.rbac import UserContext, build_table_rbac_filter, get_current_user
from backend.src.core.database import get_db_session
from backend.src.models.catalog import (
    CatalogColumn,
    CatalogTable,
    ClassificationLevel,
    DatabaseSchema,
    LifecycleStatus,
)
from backend.src.models.connections import DatabaseConnection
from backend.src.models.identity import User
from backend.src.models.rbac import (
    EntityType,
    PermissionAction,
    PermissionEffect,
)
from backend.src.models.versioning import SchemaChangeLog, SchemaChangeType
from backend.src.schemas.catalog import (
    ClassificationUpdateRequest,
    ColumnItem,
    DescriptionUpdateRequest,
    TableDetail,
    TableListResponse,
)
from backend.src.services.audit.audit_service import record_audit_event
from backend.src.services.catalog.search_service import search_service
from backend.src.services.rbac.category_service import CategoryService

router = APIRouter(tags=["Tables"])


class PhysicalTableNode(BaseModel):
    id: uuid.UUID
    name: str
    table_type: str
    row_count_estimate: int
    column_count: int


class PhysicalSchemaNode(BaseModel):
    id: uuid.UUID
    name: str
    table_count: int
    tables: List[PhysicalTableNode]


class PhysicalDatabaseNode(BaseModel):
    id: uuid.UUID
    name: str
    database_name: str
    engine_type: str
    schemas: List[PhysicalSchemaNode]


class ChangeLogItem(BaseModel):
    id: uuid.UUID
    table_id: uuid.UUID
    change_type: str
    column_name: Optional[str] = None
    old_value: Optional[Dict[str, Any]] = None
    new_value: Optional[Dict[str, Any]] = None
    actor_username: Optional[str] = None
    created_at: Any

    model_config = ConfigDict(from_attributes=True)


@router.get(
    "/tables/tree/physical",
    response_model=List[PhysicalDatabaseNode],
    summary="Get physical database hierarchy tree",
)
async def get_physical_tree(
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> List[PhysicalDatabaseNode]:
    """
    Returns full physical tree (Connection -> Schema -> Table) filtered by user RBAC VIEW_METADATA.
    """
    # 1. Fetch connections and schemas
    conn_stmt = (
        select(DatabaseConnection)
        .options(selectinload(DatabaseConnection.schemas))
        .where(DatabaseConnection.is_active == True)
        .order_by(DatabaseConnection.name.asc())
    )
    conn_res = await db.execute(conn_stmt)
    connections = conn_res.scalars().all()

    # 2. Fetch all authorized tables with column counts
    tbl_stmt = (
        select(
            CatalogTable.id,
            CatalogTable.schema_id,
            CatalogTable.name,
            CatalogTable.table_type,
            CatalogTable.row_count_estimate,
            func.count(CatalogColumn.id).label("column_count"),
        )
        .outerjoin(
            CatalogColumn,
            (CatalogColumn.table_id == CatalogTable.id) & (CatalogColumn.status != LifecycleStatus.DELETED),
        )
        .where(CatalogTable.status != LifecycleStatus.DELETED)
        .group_by(CatalogTable.id, CatalogTable.schema_id, CatalogTable.name, CatalogTable.table_type, CatalogTable.row_count_estimate)
        .order_by(CatalogTable.name.asc())
    )
    rbac_filter = build_table_rbac_filter(user_ctx=user_ctx, action=PermissionAction.VIEW_METADATA)
    tbl_stmt = rbac_filter(tbl_stmt)
    tbl_res = await db.execute(tbl_stmt)

    tables_by_schema: Dict[uuid.UUID, List[PhysicalTableNode]] = {}
    for tid, sid, tname, ttype, trows, col_count in tbl_res.all():
        if sid not in tables_by_schema:
            tables_by_schema[sid] = []
        tables_by_schema[sid].append(
            PhysicalTableNode(
                id=tid,
                name=tname,
                table_type=ttype.value if hasattr(ttype, "value") else str(ttype),
                row_count_estimate=trows,
                column_count=col_count,
            )
        )

    # 3. Assemble response
    result_tree: List[PhysicalDatabaseNode] = []
    for conn in connections:
        schema_nodes: List[PhysicalSchemaNode] = []
        for s in conn.schemas:
            s_tables = tables_by_schema.get(s.id, [])
            schema_nodes.append(
                PhysicalSchemaNode(
                    id=s.id,
                    name=s.name,
                    table_count=len(s_tables),
                    tables=s_tables,
                )
            )
        result_tree.append(
            PhysicalDatabaseNode(
                id=conn.id,
                name=conn.name,
                database_name=conn.database_name,
                engine_type=conn.engine_type.value if hasattr(conn.engine_type, "value") else str(conn.engine_type),
                schemas=schema_nodes,
            )
        )

    return result_tree


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


@router.patch(
    "/columns/{column_id}/classification",
    response_model=ColumnItem,
    summary="Update column security classification level",
)
async def update_column_classification(
    column_id: uuid.UUID,
    payload: ClassificationUpdateRequest,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ColumnItem:
    """
    Updates column security classification (PUBLIC, INTERNAL, CONFIDENTIAL, PII).
    Enforces EDIT_DESCRIPTION or MANAGE_TAGS permission and emits SchemaChangeLog and AuditLog.
    """
    column = await db.get(CatalogColumn, column_id)
    if not column or column.status == LifecycleStatus.DELETED:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Column {column_id} not found",
        )

    if not user_ctx.is_superadmin:
        effect_edit = await CategoryService.resolve_effective_permission(
            session=db,
            user_ctx=user_ctx,
            entity_type=EntityType.TABLE,
            entity_id=column.table_id,
            action=PermissionAction.EDIT_DESCRIPTION,
        )
        effect_tags = await CategoryService.resolve_effective_permission(
            session=db,
            user_ctx=user_ctx,
            entity_type=EntityType.TABLE,
            entity_id=column.table_id,
            action=PermissionAction.MANAGE_TAGS,
        )
        if effect_edit != PermissionEffect.ALLOW and effect_tags != PermissionEffect.ALLOW:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Forbidden: User lacks EDIT_DESCRIPTION or MANAGE_TAGS permission for column {column_id}",
            )

    old_level = column.classification_level.value
    column.classification_level = ClassificationLevel(payload.classification_level)

    change_log = SchemaChangeLog(
        id=uuid.uuid4(),
        table_id=column.table_id,
        change_type=SchemaChangeType.TYPE_CHANGED,
        column_name=column.name,
        old_value={"classification_level": old_level},
        new_value={"classification_level": column.classification_level.value},
        actor_id=user_ctx.user_id,
    )
    db.add(change_log)

    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user_id,
        action="EDIT_CLASSIFICATION",
        entity_type=EntityType.COLUMN,
        entity_id=column.id,
        diff_jsonb={
            "column_name": column.name,
            "old_level": old_level,
            "new_level": column.classification_level.value,
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


@router.get(
    "/tables/{table_id}/change-logs",
    response_model=List[ChangeLogItem],
    summary="Get table schema change log history",
)
async def get_table_change_logs(
    table_id: uuid.UUID,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> List[ChangeLogItem]:
    """
    Returns the complete chronological log of schema and curation changes for a table.
    """
    stmt = (
        select(SchemaChangeLog, User.username)
        .outerjoin(User, User.id == SchemaChangeLog.actor_id)
        .where(SchemaChangeLog.table_id == table_id)
        .order_by(SchemaChangeLog.created_at.desc())
    )
    res = await db.execute(stmt)
    rows = res.all()

    return [
        ChangeLogItem(
            id=log.id,
            table_id=log.table_id,
            change_type=log.change_type.value if hasattr(log.change_type, "value") else str(log.change_type),
            column_name=log.column_name,
            old_value=log.old_value,
            new_value=log.new_value,
            actor_username=uname,
            created_at=log.created_at,
        )
        for log, uname in rows
    ]
