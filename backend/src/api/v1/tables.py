from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.src.api.dependencies.rbac import UserContext, build_table_rbac_filter, get_current_user
from backend.src.core.database import get_db_session
from backend.src.models.catalog import (
    CatalogColumn,
    CatalogIndex,
    CatalogRoutine,
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
    TableOwnerUpdateRequest,
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
    host: Optional[str] = None
    port: Optional[int] = None
    owner: Optional[str] = None
    collation: Optional[str] = None
    is_active: Optional[bool] = True
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


class CatalogObjectItem(BaseModel):
    id: uuid.UUID
    name: str
    object_type: str  # TABLE, VIEW, INDEX, PROCEDURE, FUNCTION
    database_name: Optional[str] = None
    schema_name: Optional[str] = None
    table_name: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)
    source_description: Optional[str] = None
    approved_description: Optional[str] = None
    description_source: Optional[str] = "SOURCE_DB"
    owner: Optional[str] = None
    created_at: Optional[Any] = None


class CatalogObjectsResponse(BaseModel):
    counts: Dict[str, int]
    tables: List[CatalogObjectItem] = Field(default_factory=list)
    views: List[CatalogObjectItem] = Field(default_factory=list)
    indexes: List[CatalogObjectItem] = Field(default_factory=list)
    procedures: List[CatalogObjectItem] = Field(default_factory=list)
    functions: List[CatalogObjectItem] = Field(default_factory=list)


@router.get(
    "/tables/tree/physical",
    response_model=List[PhysicalDatabaseNode],
    summary="Get physical database hierarchy tree",
)
async def get_physical_tree(
    search: Optional[str] = Query(None, description="Search connection name, database, host, or owner"),
    engine_type: Optional[str] = Query(None, description="Filter by engine dialect"),
    host: Optional[str] = Query(None, description="Filter by host IP or address"),
    owner: Optional[str] = Query(None, description="Filter by database owner"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> List[PhysicalDatabaseNode]:
    """
    Returns full physical tree (Connection -> Schema -> Table) filtered by user RBAC VIEW_METADATA and optional search criteria.
    """
    # 1. Fetch connections and schemas
    conn_stmt = (
        select(DatabaseConnection)
        .options(selectinload(DatabaseConnection.schemas))
    )
    if is_active is not None:
        conn_stmt = conn_stmt.where(DatabaseConnection.is_active == is_active)
    else:
        conn_stmt = conn_stmt.where(DatabaseConnection.is_active == True)

    if engine_type is not None and engine_type.strip():
        conn_stmt = conn_stmt.where(DatabaseConnection.engine_type == engine_type.strip())
    if host is not None and host.strip():
        conn_stmt = conn_stmt.where(DatabaseConnection.host.ilike(f"%{host.strip()}%"))
    if owner is not None and owner.strip():
        conn_stmt = conn_stmt.where(DatabaseConnection.owner.ilike(f"%{owner.strip()}%"))
    if search is not None and search.strip():
        term = f"%{search.strip()}%"
        conn_stmt = conn_stmt.where(
            or_(
                DatabaseConnection.name.ilike(term),
                DatabaseConnection.database_name.ilike(term),
                DatabaseConnection.host.ilike(term),
                DatabaseConnection.owner.ilike(term),
            )
        )
    conn_stmt = conn_stmt.order_by(DatabaseConnection.name.asc())
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
                host=conn.host,
                port=conn.port,
                owner=conn.owner,
                collation=conn.collation,
                is_active=conn.is_active,
                schemas=schema_nodes,
            )
        )

    return result_tree


@router.get(
    "/catalog/objects",
    response_model=CatalogObjectsResponse,
    summary="Get grouped catalog database objects (tables, views, indexes, routines)",
)
@router.get(
    "/tables/catalog/objects",
    response_model=CatalogObjectsResponse,
    include_in_schema=False,
)
async def get_catalog_objects(
    database: Optional[str] = Query(None, description="Filter by database connection name"),
    schema: Optional[str] = Query(None, description="Filter by schema name"),
    table_id: Optional[uuid.UUID] = Query(None, description="Filter by specific table ID"),
    object_type: Optional[str] = Query(None, description="Filter category: TABLE, VIEW, INDEX, PROCEDURE, FUNCTION"),
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> CatalogObjectsResponse:
    """
    Returns categorised catalog objects with counts:
    - Tables
    - Views / Materialized Views
    - Indexes
    - Stored Procedures
    - Functions
    Filtered by user RBAC VIEW_METADATA.
    """
    # 1. Base query for Tables & Views
    table_query = (
        select(
            CatalogTable,
            DatabaseSchema.name.label("schema_name"),
            DatabaseConnection.name.label("database_name"),
        )
        .join(DatabaseSchema, DatabaseSchema.id == CatalogTable.schema_id)
        .join(DatabaseConnection, DatabaseConnection.id == DatabaseSchema.connection_id)
        .where(
            CatalogTable.status != LifecycleStatus.DELETED,
            DatabaseSchema.status != LifecycleStatus.DELETED,
        )
    )
    if database:
        table_query = table_query.where(
            or_(
                DatabaseConnection.name == database,
                DatabaseConnection.database_name == database,
            )
        )
    if schema:
        table_query = table_query.where(DatabaseSchema.name == schema)
    if table_id:
        table_query = table_query.where(CatalogTable.id == table_id)

    # RBAC filter for tables
    rbac_filter = build_table_rbac_filter(user_ctx=user_ctx, action=PermissionAction.VIEW_METADATA)
    table_query = rbac_filter(table_query)
    table_res = await db.execute(table_query)
    table_rows = table_res.all()

    tables_list: List[CatalogObjectItem] = []
    views_list: List[CatalogObjectItem] = []

    for tbl, s_name, db_name in table_rows:
        ttype = tbl.table_type.value if hasattr(tbl.table_type, "value") else str(tbl.table_type)
        item = CatalogObjectItem(
            id=tbl.id,
            name=tbl.name,
            object_type=ttype,
            database_name=db_name,
            schema_name=s_name,
            table_name=tbl.name,
            details={
                "row_count_estimate": tbl.row_count_estimate,
                "size_bytes": tbl.size_bytes,
            },
            source_description=tbl.source_description,
            approved_description=tbl.approved_description,
            description_source=getattr(tbl, "description_source", "SOURCE_DB"),
            owner=tbl.owner,
            created_at=tbl.created_at,
        )
        if ttype in ("VIEW", "MATERIALIZED_VIEW"):
            views_list.append(item)
        else:
            tables_list.append(item)

    # 2. Indexes Query
    idx_query = (
        select(
            CatalogIndex,
            CatalogTable.name.label("table_name"),
            DatabaseSchema.name.label("schema_name"),
            DatabaseConnection.name.label("database_name"),
        )
        .join(CatalogTable, CatalogTable.id == CatalogIndex.table_id)
        .join(DatabaseSchema, DatabaseSchema.id == CatalogIndex.schema_id)
        .join(DatabaseConnection, DatabaseConnection.id == DatabaseSchema.connection_id)
        .where(
            CatalogTable.status != LifecycleStatus.DELETED,
            DatabaseSchema.status != LifecycleStatus.DELETED,
        )
    )
    if database:
        idx_query = idx_query.where(
            or_(
                DatabaseConnection.name == database,
                DatabaseConnection.database_name == database,
            )
        )
    if schema:
        idx_query = idx_query.where(DatabaseSchema.name == schema)
    if table_id:
        idx_query = idx_query.where(CatalogIndex.table_id == table_id)

    idx_res = await db.execute(idx_query)
    idx_rows = idx_res.all()

    indexes_list: List[CatalogObjectItem] = []
    for idx_obj, t_name, s_name, db_name in idx_rows:
        indexes_list.append(
            CatalogObjectItem(
                id=idx_obj.id,
                name=idx_obj.name,
                object_type="INDEX",
                database_name=db_name,
                schema_name=s_name,
                table_name=t_name,
                details={
                    "index_type": idx_obj.index_type,
                    "is_unique": idx_obj.is_unique,
                    "is_primary": idx_obj.is_primary,
                    "columns_list": idx_obj.columns_list or [],
                    "definition": idx_obj.definition,
                    "table_id": str(idx_obj.table_id),
                },
                created_at=idx_obj.created_at,
            )
        )

    # 3. Routines (Procedures & Functions) Query
    procedures_list: List[CatalogObjectItem] = []
    functions_list: List[CatalogObjectItem] = []

    # If table_id is specified, procedures and functions don't belong to a specific table directly
    if not table_id:
        routine_query = (
            select(
                CatalogRoutine,
                DatabaseSchema.name.label("schema_name"),
                DatabaseConnection.name.label("database_name"),
            )
            .join(DatabaseSchema, DatabaseSchema.id == CatalogRoutine.schema_id)
            .join(DatabaseConnection, DatabaseConnection.id == DatabaseSchema.connection_id)
            .where(DatabaseSchema.status != LifecycleStatus.DELETED)
        )
        if database:
            routine_query = routine_query.where(
                or_(
                    DatabaseConnection.name == database,
                    DatabaseConnection.database_name == database,
                )
            )
        if schema:
            routine_query = routine_query.where(DatabaseSchema.name == schema)

        routine_res = await db.execute(routine_query)
        routine_rows = routine_res.all()

        for r_obj, s_name, db_name in routine_rows:
            r_type = (r_obj.routine_type or "FUNCTION").upper()
            item = CatalogObjectItem(
                id=r_obj.id,
                name=r_obj.name,
                object_type=r_type,
                database_name=db_name,
                schema_name=s_name,
                details={
                    "language": r_obj.language,
                    "arguments_signature": r_obj.arguments_signature,
                    "return_type": r_obj.return_type,
                },
                source_description=r_obj.source_description,
                approved_description=r_obj.approved_description,
                description_source="SOURCE_DB",
                created_at=r_obj.created_at,
            )
            if r_type == "PROCEDURE":
                procedures_list.append(item)
            else:
                functions_list.append(item)

    counts = {
        "tables": len(tables_list),
        "views": len(views_list),
        "indexes": len(indexes_list),
        "procedures": len(procedures_list),
        "functions": len(functions_list),
    }

    req_type = (object_type or "").upper()
    return CatalogObjectsResponse(
        counts=counts,
        tables=tables_list if not req_type or req_type in ("TABLE", "ALL") else [],
        views=views_list if not req_type or req_type in ("VIEW", "ALL") else [],
        indexes=indexes_list if not req_type or req_type in ("INDEX", "ALL") else [],
        procedures=procedures_list if not req_type or req_type in ("PROCEDURE", "ALL") else [],
        functions=functions_list if not req_type or req_type in ("FUNCTION", "ALL") else [],
    )


@router.get(
    "/schemas/{schema_id}/tables",
    response_model=TableListResponse,
    summary="Keyset-paginated tables for a schema",
)
async def list_schema_tables(
    schema_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200, description="Max results per page"),
    cursor: Optional[str] = Query(None, description="Base64-encoded keyset cursor"),
    missing_descriptions_only: bool = Query(False, description="Filter for items with missing descriptions"),
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
        missing_descriptions_only=missing_descriptions_only,
    )
    return TableListResponse(items=items, page_info=page_info)


@router.get(
    "/tables",
    response_model=TableListResponse,
    summary="List tables with optional missing descriptions filter",
)
async def list_all_tables(
    schema_id: Optional[uuid.UUID] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    cursor: Optional[str] = Query(None),
    missing_descriptions_only: bool = Query(False),
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> TableListResponse:
    if schema_id:
        items, page_info = await search_service.list_tables_by_schema(
            session=db,
            schema_id=schema_id,
            user_ctx=user_ctx,
            limit=limit,
            cursor=cursor,
            missing_descriptions_only=missing_descriptions_only,
        )
        return TableListResponse(items=items, page_info=page_info)

    # Global table list with RBAC
    stmt = select(CatalogTable).where(CatalogTable.status != LifecycleStatus.DELETED)
    if missing_descriptions_only:
        stmt = stmt.where(
            or_(
                CatalogTable.effective_description.is_(None),
                CatalogTable.effective_description == "",
            )
        )
    rbac_filter = build_table_rbac_filter(user_ctx=user_ctx, action=PermissionAction.VIEW_METADATA)
    stmt = rbac_filter(stmt)
    stmt = stmt.order_by(CatalogTable.name.asc(), CatalogTable.id.asc()).limit(limit)
    res = await db.execute(stmt)
    tables = res.scalars().all()
    items = [
        {
            "id": t.id,
            "schema_id": t.schema_id,
            "name": t.name,
            "table_type": t.table_type.value,
            "row_count_estimate": t.row_count_estimate,
            "size_bytes": t.size_bytes,
            "source_description": t.source_description,
            "approved_description": t.approved_description,
            "effective_description": t.effective_description,
            "ai_suggested_description": t.ai_suggested_description,
            "description_source": t.description_source,
            "curated_by_id": t.curated_by_id,
            "curated_by_username": t.curated_by_username,
            "curated_at": t.curated_at,
            "owner": t.owner,
            "status": t.status.value,
            "tags": [],
        }
        for t in tables
    ]
    return TableListResponse(items=items, page_info={"has_next_page": False, "next_cursor": None, "limit": limit})


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
    now = datetime.now(timezone.utc)
    table.approved_description = new_desc
    table.description_source = "ADMIN_MANUAL"
    table.curated_by_id = user_ctx.user_id
    table.curated_by_username = user_ctx.user.username
    table.curated_at = now

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
            "description_source": "ADMIN_MANUAL",
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
    "/tables/{table_id}/owner",
    response_model=TableDetail,
    summary="Inline edit of table owner",
)
async def update_table_owner(
    table_id: uuid.UUID,
    payload: TableOwnerUpdateRequest,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> TableDetail:
    """
    Updates the owner of a catalog table.
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

    old_owner = table.owner
    new_owner = payload.owner.strip() if payload.owner else None
    table.owner = new_owner

    # Insert SchemaChangeLog
    change_log = SchemaChangeLog(
        id=uuid.uuid4(),
        table_id=table.id,
        change_type=SchemaChangeType.DESCRIPTION_UPDATED,
        column_name=None,
        old_value={"owner": old_owner},
        new_value={"owner": new_owner},
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
            "field": "owner",
            "old_value": old_owner,
            "new_value": new_owner,
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


@router.post(
    "/tables/{table_id}/approve-ai-description",
    response_model=TableDetail,
    summary="Approve AI-suggested description for a table",
)
async def approve_table_ai_description(
    table_id: uuid.UUID,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> TableDetail:
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

    if not table.ai_suggested_description:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="پیشنهاد هوش مصنوعی برای این جدول ثبت نشده است.",
        )

    old_desc = table.approved_description
    new_desc = table.ai_suggested_description
    now = datetime.now(timezone.utc)

    table.approved_description = new_desc
    table.description_source = "AI_APPROVED"
    table.curated_by_id = user_ctx.user_id
    table.curated_by_username = user_ctx.user.username
    table.curated_at = now

    change_log = SchemaChangeLog(
        id=uuid.uuid4(),
        table_id=table.id,
        change_type=SchemaChangeType.DESCRIPTION_UPDATED,
        column_name=None,
        old_value={"approved_description": old_desc},
        new_value={"approved_description": new_desc, "source": "AI_APPROVED"},
        actor_id=user_ctx.user_id,
    )
    db.add(change_log)

    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user_id,
        action="APPROVE_AI_DESCRIPTION",
        entity_type=EntityType.TABLE,
        entity_id=table.id,
        diff_jsonb={
            "field": "approved_description",
            "old_value": old_desc,
            "new_value": new_desc,
            "description_source": "AI_APPROVED",
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
            detail=f"Table {table_id} could not be retrieved after approval",
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
    now = datetime.now(timezone.utc)
    column.approved_description = new_desc
    column.description_source = "ADMIN_MANUAL"
    column.curated_by_id = user_ctx.user_id
    column.curated_by_username = user_ctx.user.username
    column.curated_at = now

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
            "description_source": "ADMIN_MANUAL",
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
        ai_suggested_description=column.ai_suggested_description,
        description_source=column.description_source,
        curated_by_id=column.curated_by_id,
        curated_by_username=column.curated_by_username,
        curated_at=column.curated_at,
        classification_level=column.classification_level.value,
        status=column.status.value,
    )


@router.post(
    "/columns/{column_id}/approve-ai-description",
    response_model=ColumnItem,
    summary="Approve AI-suggested description for a column",
)
async def approve_column_ai_description(
    column_id: uuid.UUID,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ColumnItem:
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

    if not column.ai_suggested_description:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="پیشنهاد هوش مصنوعی برای این ستون ثبت نشده است.",
        )

    old_desc = column.approved_description
    new_desc = column.ai_suggested_description
    now = datetime.now(timezone.utc)

    column.approved_description = new_desc
    column.description_source = "AI_APPROVED"
    column.curated_by_id = user_ctx.user_id
    column.curated_by_username = user_ctx.user.username
    column.curated_at = now

    change_log = SchemaChangeLog(
        id=uuid.uuid4(),
        table_id=column.table_id,
        change_type=SchemaChangeType.DESCRIPTION_UPDATED,
        column_name=column.name,
        old_value={"approved_description": old_desc},
        new_value={"approved_description": new_desc, "source": "AI_APPROVED"},
        actor_id=user_ctx.user_id,
    )
    db.add(change_log)

    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user_id,
        action="APPROVE_AI_DESCRIPTION",
        entity_type=EntityType.COLUMN,
        entity_id=column.id,
        diff_jsonb={
            "field": "approved_description",
            "table_id": str(column.table_id),
            "column_name": column.name,
            "old_value": old_desc,
            "new_value": new_desc,
            "description_source": "AI_APPROVED",
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
        ai_suggested_description=column.ai_suggested_description,
        description_source=column.description_source,
        curated_by_id=column.curated_by_id,
        curated_by_username=column.curated_by_username,
        curated_at=column.curated_at,
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
        ai_suggested_description=column.ai_suggested_description,
        description_source=column.description_source,
        curated_by_id=column.curated_by_id,
        curated_by_username=column.curated_by_username,
        curated_at=column.curated_at,
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
