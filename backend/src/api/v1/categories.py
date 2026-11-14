from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.api.dependencies.rbac import (
    UserContext,
    build_table_rbac_filter,
    get_current_user,
    require_permission,
)
from backend.src.core.database import get_db_session
from backend.src.models.catalog import CatalogColumn, CatalogTable, LifecycleStatus, DatabaseSchema
from backend.src.models.connections import DatabaseConnection
from backend.src.models.rbac import (
    Category,
    CategoryAssignment,
    EntityType,
    PermissionAction,
)
from backend.src.services.audit.audit_service import record_audit_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/categories", tags=["Categories"])


class CategoryCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    parent_id: Optional[uuid.UUID] = None
    status: Optional[str] = Field(default="ACTIVE", pattern="^(ACTIVE|INACTIVE)$")


class CategoryUpdateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    parent_id: Optional[uuid.UUID] = None
    status: Optional[str] = Field(default="ACTIVE", pattern="^(ACTIVE|INACTIVE)$")


class CategoryResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: Optional[str] = None
    parent_id: Optional[uuid.UUID] = None
    status: str = "ACTIVE"
    assigned_tables_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class CategoryTreeNode(BaseModel):
    id: uuid.UUID
    name: str
    description: Optional[str] = None
    parent_id: Optional[uuid.UUID] = None
    status: str = "ACTIVE"
    assigned_tables_count: int = 0
    tables: List[Dict[str, Any]] = Field(default_factory=list)
    databases: List[Dict[str, Any]] = Field(default_factory=list)
    children: List[CategoryTreeNode] = Field(default_factory=list)


class CategoryAssignmentRequest(BaseModel):
    category_id: uuid.UUID
    entity_type: str = Field(default="TABLE", pattern="^(TABLE|COLUMN|DATABASE|SCHEMA)$")
    entity_id: uuid.UUID


class CategoryAssignmentItem(BaseModel):
    id: uuid.UUID
    category_id: uuid.UUID
    category_name: Optional[str] = None
    entity_type: str
    entity_id: uuid.UUID
    table_name: Optional[str] = None
    schema_name: Optional[str] = None
    database_name: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class AvailableTableItem(BaseModel):
    id: uuid.UUID
    name: str
    schema_name: str
    database_name: str
    row_count_estimate: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)


@router.get("", response_model=List[CategoryResponse], summary="List all business categories")
async def list_categories(
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> List[CategoryResponse]:
    """Lists all business categories with table assignment counts."""
    stmt = (
        select(
            Category,
            func.count(CategoryAssignment.id).label("table_count"),
        )
        .outerjoin(
            CategoryAssignment,
            (CategoryAssignment.category_id == Category.id)
            & (CategoryAssignment.entity_type == EntityType.TABLE),
        )
        .group_by(Category.id)
        .order_by(Category.name.asc())
    )
    result = await db.execute(stmt)
    rows = result.all()

    return [
        CategoryResponse(
            id=cat.id,
            name=cat.name,
            description=cat.description,
            parent_id=cat.parent_id,
            status=cat.status if hasattr(cat, "status") and cat.status else "ACTIVE",
            assigned_tables_count=count_val,
        )
        for cat, count_val in rows
    ]


@router.get("/tree", response_model=List[CategoryTreeNode], summary="Hierarchical Category Tree with assigned tables")
async def get_category_tree(
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> List[CategoryTreeNode]:
    """
    Returns the recursive Business Category Tree populated with assigned tables authorized
    for the user via SQL-pushed RBAC.
    """
    cat_stmt = select(Category).order_by(Category.name.asc())
    cat_res = await db.execute(cat_stmt)
    all_categories = list(cat_res.scalars().all())

    # Fetch assigned tables authorized for user
    tbl_stmt = (
        select(
            CatalogTable.id,
            CatalogTable.name,
            CatalogTable.row_count_estimate,
            CategoryAssignment.category_id,
            func.count(CatalogColumn.id).label("column_count"),
            DatabaseSchema.name.label("schema_name"),
            DatabaseConnection.name.label("database_name"),
        )
        .join(
            CategoryAssignment,
            (CategoryAssignment.entity_id == CatalogTable.id)
            & (CategoryAssignment.entity_type == EntityType.TABLE),
        )
        .join(DatabaseSchema, DatabaseSchema.id == CatalogTable.schema_id)
        .join(DatabaseConnection, DatabaseConnection.id == DatabaseSchema.connection_id)
        .outerjoin(CatalogColumn, (CatalogColumn.table_id == CatalogTable.id) & (CatalogColumn.status != LifecycleStatus.DELETED))
        .where(CatalogTable.status != LifecycleStatus.DELETED)
        .group_by(CatalogTable.id, CatalogTable.name, CatalogTable.row_count_estimate, CategoryAssignment.category_id, DatabaseSchema.name, DatabaseConnection.name)
        .order_by(CatalogTable.name.asc())
    )

    rbac_filter = build_table_rbac_filter(user_ctx=user_ctx, action=PermissionAction.VIEW_METADATA)
    tbl_stmt = rbac_filter(tbl_stmt)

    tbl_res = await db.execute(tbl_stmt)
    tables_by_cat: Dict[uuid.UUID, List[Dict[str, Any]]] = {}
    for tid, tname, trows, cat_id, col_count, sname, dbname in tbl_res.all():
        if cat_id not in tables_by_cat:
            tables_by_cat[cat_id] = []
        tables_by_cat[cat_id].append({
            "id": tid,
            "name": tname,
            "schema_name": sname,
            "database_name": dbname,
            "row_count_estimate": trows,
            "column_count": col_count,
        })

    # Fetch assigned databases
    db_stmt = (
        select(
            DatabaseConnection.id,
            DatabaseConnection.name,
            DatabaseConnection.engine_type,
            DatabaseConnection.database_name,
            CategoryAssignment.category_id,
        )
        .join(
            CategoryAssignment,
            (CategoryAssignment.entity_id == DatabaseConnection.id)
            & (CategoryAssignment.entity_type == EntityType.DATABASE),
        )
        .order_by(DatabaseConnection.name.asc())
    )
    db_res = await db.execute(db_stmt)
    dbs_by_cat: Dict[uuid.UUID, List[Dict[str, Any]]] = {}
    for did, dname, deng, ddbname, cat_id in db_res.all():
        if cat_id not in dbs_by_cat:
            dbs_by_cat[cat_id] = []
        dbs_by_cat[cat_id].append({
            "id": did,
            "name": dname,
            "engine_type": deng.value if hasattr(deng, "value") else str(deng),
            "database_name": ddbname,
        })

    # Build recursive hierarchy
    nodes_by_id: Dict[uuid.UUID, CategoryTreeNode] = {}
    for c in all_categories:
        cat_tables = tables_by_cat.get(c.id, [])
        nodes_by_id[c.id] = CategoryTreeNode(
            id=c.id,
            name=c.name,
            description=c.description,
            parent_id=c.parent_id,
            status=c.status if hasattr(c, "status") and c.status else "ACTIVE",
            assigned_tables_count=len(cat_tables),
            tables=cat_tables,
            databases=dbs_by_cat.get(c.id, []),
            children=[],
        )

    root_nodes: List[CategoryTreeNode] = []
    for c in all_categories:
        node = nodes_by_id[c.id]
        if c.parent_id and c.parent_id in nodes_by_id:
            nodes_by_id[c.parent_id].children.append(node)
        else:
            root_nodes.append(node)

    return root_nodes


@router.post("", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED, summary="Create a new category")
async def create_category(
    payload: CategoryCreateRequest,
    user_ctx: UserContext = Depends(require_permission(PermissionAction.MANAGE_TAGS)),
    db: AsyncSession = Depends(get_db_session),
) -> CategoryResponse:
    if payload.parent_id:
        parent = await db.get(Category, payload.parent_id)
        if not parent:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="والد مشخص شده یافت نشد")

    cat = Category(
        id=uuid.uuid4(),
        name=payload.name,
        description=payload.description,
        parent_id=payload.parent_id,
        status=payload.status or "ACTIVE",
    )
    db.add(cat)
    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user.id,
        action="CREATE_CATEGORY",
        entity_type=EntityType.SCHEMA,
        entity_id=cat.id,
        diff_jsonb={"name": cat.name, "parent_id": str(cat.parent_id) if cat.parent_id else None, "status": cat.status},
        flush=True,
    )
    await db.flush()
    return CategoryResponse(
        id=cat.id,
        name=cat.name,
        description=cat.description,
        parent_id=cat.parent_id,
        status=cat.status,
        assigned_tables_count=0,
    )


@router.put("/{category_id}", response_model=CategoryResponse, summary="Update a category")
async def update_category(
    category_id: uuid.UUID,
    payload: CategoryUpdateRequest,
    user_ctx: UserContext = Depends(require_permission(PermissionAction.MANAGE_TAGS)),
    db: AsyncSession = Depends(get_db_session),
) -> CategoryResponse:
    cat = await db.get(Category, category_id)
    if not cat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")

    if payload.parent_id:
        if payload.parent_id == category_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="دسته‌بندی نمی‌تواند والد خودش باشد")
        parent = await db.get(Category, payload.parent_id)
        if not parent:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="والد مشخص شده یافت نشد")

    old_name = cat.name
    cat.name = payload.name
    cat.description = payload.description
    cat.parent_id = payload.parent_id
    cat.status = payload.status or "ACTIVE"

    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user.id,
        action="UPDATE_CATEGORY",
        entity_type=EntityType.SCHEMA,
        entity_id=cat.id,
        diff_jsonb={"old_name": old_name, "new_name": cat.name, "status": cat.status},
        flush=True,
    )
    await db.flush()

    count_stmt = select(func.count(CategoryAssignment.id)).where(
        (CategoryAssignment.category_id == cat.id) & (CategoryAssignment.entity_type == EntityType.TABLE)
    )
    assign_count = (await db.execute(count_stmt)).scalar() or 0

    return CategoryResponse(
        id=cat.id,
        name=cat.name,
        description=cat.description,
        parent_id=cat.parent_id,
        status=cat.status,
        assigned_tables_count=assign_count,
    )


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a category")
async def delete_category(
    category_id: uuid.UUID,
    user_ctx: UserContext = Depends(require_permission(PermissionAction.MANAGE_TAGS)),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    cat = await db.get(Category, category_id)
    if not cat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")

    # Safety check 1: child categories
    child_count_stmt = select(func.count(Category.id)).where(Category.parent_id == category_id)
    child_count = (await db.execute(child_count_stmt)).scalar() or 0
    if child_count > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="امکان حذف دسته‌بندی دارای زیردسته وجود ندارد. ابتدا زیردسته‌ها را حذف یا منتقل نمایید.",
        )

    # Safety check 2: assigned entities (tables, etc.)
    assign_count_stmt = select(func.count(CategoryAssignment.id)).where(CategoryAssignment.category_id == category_id)
    assign_count = (await db.execute(assign_count_stmt)).scalar() or 0
    if assign_count > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="امکان حذف دسته‌بندی دارای جداول اختصاص‌یافته وجود ندارد. ابتدا تخصیص‌های جداول را لغو نمایید.",
        )

    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user.id,
        action="DELETE_CATEGORY",
        entity_type=EntityType.SCHEMA,
        entity_id=cat.id,
        diff_jsonb={"deleted_name": cat.name},
        flush=True,
    )
    await db.delete(cat)
    await db.flush()


@router.get("/assignments", response_model=List[CategoryAssignmentItem], summary="Get category assignments")
async def get_category_assignments(
    category_id: Optional[uuid.UUID] = Query(None, description="Filter by category ID"),
    entity_type: Optional[str] = Query(None, description="Filter by entity type (e.g. TABLE)"),
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> List[CategoryAssignmentItem]:
    stmt = (
        select(
            CategoryAssignment.id,
            CategoryAssignment.category_id,
            Category.name.label("category_name"),
            CategoryAssignment.entity_type,
            CategoryAssignment.entity_id,
            CatalogTable.name.label("table_name"),
            DatabaseSchema.name.label("schema_name"),
            DatabaseConnection.name.label("database_name"),
            CategoryAssignment.created_at,
        )
        .join(Category, Category.id == CategoryAssignment.category_id)
        .outerjoin(
            CatalogTable,
            (CatalogTable.id == CategoryAssignment.entity_id) & (CategoryAssignment.entity_type == EntityType.TABLE),
        )
        .outerjoin(DatabaseSchema, DatabaseSchema.id == CatalogTable.schema_id)
        .outerjoin(DatabaseConnection, DatabaseConnection.id == DatabaseSchema.connection_id)
        .order_by(CategoryAssignment.created_at.desc())
    )
    if category_id:
        stmt = stmt.where(CategoryAssignment.category_id == category_id)
    if entity_type:
        stmt = stmt.where(CategoryAssignment.entity_type == EntityType(entity_type))

    res = await db.execute(stmt)
    rows = res.all()
    return [
        CategoryAssignmentItem(
            id=r[0],
            category_id=r[1],
            category_name=r[2],
            entity_type=r[3].value if hasattr(r[3], "value") else str(r[3]),
            entity_id=r[4],
            table_name=r[5],
            schema_name=r[6],
            database_name=r[7],
            created_at=r[8],
        )
        for r in rows
    ]


@router.post("/assignments", status_code=status.HTTP_201_CREATED, summary="Assign entity to category")
async def assign_category(
    payload: CategoryAssignmentRequest,
    user_ctx: UserContext = Depends(require_permission(PermissionAction.MANAGE_TAGS)),
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    cat = await db.get(Category, payload.category_id)
    if not cat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")

    entity_enum = EntityType(payload.entity_type)
    check_stmt = select(CategoryAssignment).where(
        CategoryAssignment.category_id == payload.category_id,
        CategoryAssignment.entity_type == entity_enum,
        CategoryAssignment.entity_id == payload.entity_id,
    )
    existing = (await db.execute(check_stmt)).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="این موجودیت پیش از این به دسته‌بندی اختصاص یافته است.",
        )

    assignment = CategoryAssignment(
        id=uuid.uuid4(),
        category_id=payload.category_id,
        entity_type=entity_enum,
        entity_id=payload.entity_id,
    )
    db.add(assignment)
    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user.id,
        action="ASSIGN_CATEGORY",
        entity_type=entity_enum,
        entity_id=payload.entity_id,
        diff_jsonb={"category_id": str(payload.category_id), "category_name": cat.name},
        flush=True,
    )
    await db.flush()

    return {
        "success": True,
        "id": str(assignment.id),
        "category_id": str(payload.category_id),
        "entity_type": payload.entity_type,
        "entity_id": str(payload.entity_id),
    }


@router.delete("/assignments/{category_id}/{entity_type}/{entity_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Remove assignment by compound key")
async def unassign_category(
    category_id: uuid.UUID,
    entity_type: str,
    entity_id: uuid.UUID,
    user_ctx: UserContext = Depends(require_permission(PermissionAction.MANAGE_TAGS)),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    entity_enum = EntityType(entity_type)
    stmt = select(CategoryAssignment).where(
        CategoryAssignment.category_id == category_id,
        CategoryAssignment.entity_type == entity_enum,
        CategoryAssignment.entity_id == entity_id,
    )
    res = await db.execute(stmt)
    assignment = res.scalar_one_or_none()
    if assignment:
        await record_audit_event(
            session=db,
            actor_username=user_ctx.user.username,
            actor_id=user_ctx.user.id,
            action="UNASSIGN_CATEGORY",
            entity_type=entity_enum,
            entity_id=entity_id,
            diff_jsonb={"category_id": str(category_id)},
            flush=True,
        )
        await db.delete(assignment)
        await db.flush()


@router.delete("/assignments/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Remove assignment by ID")
async def unassign_category_by_id(
    assignment_id: uuid.UUID,
    user_ctx: UserContext = Depends(require_permission(PermissionAction.MANAGE_TAGS)),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    assignment = await db.get(CategoryAssignment, assignment_id)
    if assignment:
        await record_audit_event(
            session=db,
            actor_username=user_ctx.user.username,
            actor_id=user_ctx.user.id,
            action="UNASSIGN_CATEGORY",
            entity_type=assignment.entity_type,
            entity_id=assignment.entity_id,
            diff_jsonb={"category_id": str(assignment.category_id)},
            flush=True,
        )
        await db.delete(assignment)
        await db.flush()


@router.get("/available-tables", response_model=List[AvailableTableItem], summary="List catalog tables available for assignment")
async def list_available_tables(
    search: Optional[str] = Query(None, description="Search term for table, schema, or database"),
    limit: int = Query(50, ge=1, le=200),
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> List[AvailableTableItem]:
    stmt = (
        select(
            CatalogTable.id,
            CatalogTable.name,
            DatabaseSchema.name.label("schema_name"),
            DatabaseConnection.name.label("database_name"),
            CatalogTable.row_count_estimate,
        )
        .join(DatabaseSchema, DatabaseSchema.id == CatalogTable.schema_id)
        .join(DatabaseConnection, DatabaseConnection.id == DatabaseSchema.connection_id)
        .where(CatalogTable.status != LifecycleStatus.DELETED)
        .order_by(CatalogTable.name.asc())
        .limit(limit)
    )
    if search:
        search_filter = f"%{search}%"
        stmt = stmt.where(
            or_(
                CatalogTable.name.ilike(search_filter),
                DatabaseSchema.name.ilike(search_filter),
                DatabaseConnection.name.ilike(search_filter),
            )
        )
    rbac_filter = build_table_rbac_filter(user_ctx=user_ctx, action=PermissionAction.VIEW_METADATA)
    stmt = rbac_filter(stmt)
    res = await db.execute(stmt)
    return [
        AvailableTableItem(
            id=row[0],
            name=row[1],
            schema_name=row[2],
            database_name=row[3],
            row_count_estimate=row[4],
        )
        for row in res.all()
    ]
