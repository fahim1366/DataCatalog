from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.api.dependencies.rbac import UserContext, build_table_rbac_filter, get_current_user
from backend.src.core.database import get_db_session
from backend.src.models.catalog import CatalogColumn, CatalogTable, LifecycleStatus
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
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    parent_id: Optional[uuid.UUID] = None


class CategoryUpdateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    parent_id: Optional[uuid.UUID] = None


class CategoryResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: Optional[str] = None
    parent_id: Optional[uuid.UUID] = None
    assigned_tables_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class CategoryTreeNode(BaseModel):
    id: uuid.UUID
    name: str
    description: Optional[str] = None
    parent_id: Optional[uuid.UUID] = None
    tables: List[Dict[str, Any]] = Field(default_factory=list)
    children: List[CategoryTreeNode] = Field(default_factory=list)


class CategoryAssignmentRequest(BaseModel):
    category_id: uuid.UUID
    entity_type: str = Field(default="TABLE", pattern="^(TABLE|COLUMN|DATABASE|SCHEMA)$")
    entity_id: uuid.UUID


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
        )
        .join(
            CategoryAssignment,
            (CategoryAssignment.entity_id == CatalogTable.id)
            & (CategoryAssignment.entity_type == EntityType.TABLE),
        )
        .outerjoin(CatalogColumn, (CatalogColumn.table_id == CatalogTable.id) & (CatalogColumn.status != LifecycleStatus.DELETED))
        .where(CatalogTable.status != LifecycleStatus.DELETED)
        .group_by(CatalogTable.id, CatalogTable.name, CatalogTable.row_count_estimate, CategoryAssignment.category_id)
        .order_by(CatalogTable.name.asc())
    )

    rbac_filter = build_table_rbac_filter(user_ctx=user_ctx, action=PermissionAction.VIEW_METADATA)
    tbl_stmt = rbac_filter(tbl_stmt)

    tbl_res = await db.execute(tbl_stmt)
    tables_by_cat: Dict[uuid.UUID, List[Dict[str, Any]]] = {}
    for tid, tname, trows, cat_id, col_count in tbl_res.all():
        if cat_id not in tables_by_cat:
            tables_by_cat[cat_id] = []
        tables_by_cat[cat_id].append({
            "id": tid,
            "name": tname,
            "row_count_estimate": trows,
            "column_count": col_count,
        })

    # Build recursive hierarchy
    nodes_by_id: Dict[uuid.UUID, CategoryTreeNode] = {}
    for c in all_categories:
        nodes_by_id[c.id] = CategoryTreeNode(
            id=c.id,
            name=c.name,
            description=c.description,
            parent_id=c.parent_id,
            tables=tables_by_cat.get(c.id, []),
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
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> CategoryResponse:
    cat = Category(
        id=uuid.uuid4(),
        name=payload.name,
        description=payload.description,
        parent_id=payload.parent_id,
    )
    db.add(cat)
    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user.id,
        action="CREATE_CATEGORY",
        entity_type=EntityType.SCHEMA,
        entity_id=cat.id,
        diff_jsonb={"name": cat.name, "parent_id": str(cat.parent_id) if cat.parent_id else None},
        flush=True,
    )
    await db.flush()
    return CategoryResponse(
        id=cat.id,
        name=cat.name,
        description=cat.description,
        parent_id=cat.parent_id,
        assigned_tables_count=0,
    )


@router.put("/{category_id}", response_model=CategoryResponse, summary="Update a category")
async def update_category(
    category_id: uuid.UUID,
    payload: CategoryUpdateRequest,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> CategoryResponse:
    cat = await db.get(Category, category_id)
    if not cat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")

    old_name = cat.name
    cat.name = payload.name
    cat.description = payload.description
    cat.parent_id = payload.parent_id

    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user.id,
        action="UPDATE_CATEGORY",
        entity_type=EntityType.SCHEMA,
        entity_id=cat.id,
        diff_jsonb={"old_name": old_name, "new_name": cat.name},
        flush=True,
    )
    await db.flush()
    return CategoryResponse(
        id=cat.id,
        name=cat.name,
        description=cat.description,
        parent_id=cat.parent_id,
        assigned_tables_count=0,
    )


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a category")
async def delete_category(
    category_id: uuid.UUID,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    cat = await db.get(Category, category_id)
    if not cat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")

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


@router.post("/assignments", status_code=status.HTTP_201_CREATED, summary="Assign entity to category")
async def assign_category(
    payload: CategoryAssignmentRequest,
    user_ctx: UserContext = Depends(get_current_user),
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
    if not existing:
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

    return {"success": True, "category_id": str(payload.category_id), "entity_id": str(payload.entity_id)}


@router.delete("/assignments/{category_id}/{entity_type}/{entity_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Remove assignment")
async def unassign_category(
    category_id: uuid.UUID,
    entity_type: str,
    entity_id: uuid.UUID,
    user_ctx: UserContext = Depends(get_current_user),
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
