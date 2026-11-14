from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.src.api.dependencies.rbac import UserContext, get_current_user
from backend.src.core.database import get_db_session
from backend.src.models.identity import GroupRole, Role, User, UserGroup, UserRole
from backend.src.models.rbac import (
    EntityType,
    PermissionAction,
    PermissionEffect,
    PermissionRule,
    ResourceType,
)
from backend.src.services.audit.audit_service import record_audit_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/roles", tags=["Roles"])


class RoleCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    description: Optional[str] = None


class RoleUpdateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    description: Optional[str] = None


class RoleResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: Optional[str] = None
    is_system_role: bool
    user_count: int = 0
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PaginatedRoleResponse(BaseModel):
    items: List[RoleResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class RolePermissionRuleItem(BaseModel):
    resource_type: ResourceType
    resource_id: Optional[uuid.UUID] = None
    action: PermissionAction
    effect: PermissionEffect = PermissionEffect.ALLOW


class RolePermissionsUpdateRequest(BaseModel):
    permissions: List[RolePermissionRuleItem]


class RoleUserItem(BaseModel):
    id: uuid.UUID
    username: str
    full_name: str
    email: str
    assignment_type: str  # 'DIRECT' | 'VIA_GROUP'
    group_name: Optional[str] = None


@router.get(
    "/permissions-registry",
    response_model=List[Dict[str, Any]],
    summary="Get registry of available system permissions grouped by module",
)
async def get_permissions_registry(
    user_ctx: UserContext = Depends(get_current_user),
) -> List[Dict[str, Any]]:
    """Returns all available system permissions categorized by functional module."""
    return [
        {
            "module_key": "CATALOG_METADATA",
            "module_name": "کاتالوگ داده و متاداده",
            "description": "دسترسی به ساختار پایگاه‌های داده، جداول، ستون‌ها و مستندات",
            "permissions": [
                {
                    "action": "VIEW_METADATA",
                    "name": "مشاهده متاداده",
                    "description": "مشاهده ساختار جداول، نوع ستون‌ها، اندازه‌ها و مشخصات پایه کاتالوگ",
                },
                {
                    "action": "EDIT_DESCRIPTION",
                    "name": "ویرایش توضیحات تجاری",
                    "description": "ثبت و اصلاح مستندات، توضیحات تجاری ستون‌ها و واژه‌نامه داده",
                },
                {
                    "action": "MANAGE_TAGS",
                    "name": "مدیریت برچسب و طبقه‌بندی",
                    "description": "انتساب برچسب‌های تجاری و طبقه‌بندی سطوح امنیت و PII",
                },
            ],
        },
        {
            "module_key": "CONNECTIONS_PIPELINES",
            "module_name": "اتصالات و خطوط داده",
            "description": "مدیریت ارتباط با سامانه‌های مبدأ و فرآیندهای بلع اطلاعات",
            "permissions": [
                {
                    "action": "MANAGE_CONNECTIONS",
                    "name": "مدیریت اتصالات",
                    "description": "تعریف، ویرایش، حذف و همگام‌سازی زمان‌بندی‌شده پایگاه‌های داده",
                },
            ],
        },
        {
            "module_key": "SECURITY_AUDIT",
            "module_name": "امنیت و حسابرسی",
            "description": "پایش تاریخچه وقایع، لاگ‌های سیستمی و تغییرات امنیتی",
            "permissions": [
                {
                    "action": "VIEW_AUDIT_LOGS",
                    "name": "مشاهده لاگ‌های حسابرسی",
                    "description": "پایش رویدادهای دسترسی، تغییر دسترسی کاربران و فعالیت‌های سیستمی",
                },
            ],
        },
        {
            "module_key": "REPORTS_ANALYTICS",
            "module_name": "گزارش‌ها و تحلیل‌ها",
            "description": "داشبوردهای مدیریتی، پوشش مستندات و کیفیت داده",
            "permissions": [
                {
                    "action": "EXPORT_REPORTS",
                    "name": "خروجی گزارش‌ها",
                    "description": "دریافت خروجی گزارش‌های حاکمیت داده، سلامت و شناسنامه کاتالوگ",
                },
            ],
        },
    ]


@router.get(
    "",
    response_model=Union[PaginatedRoleResponse, List[RoleResponse]],
    summary="List all roles (supports pagination and search)",
)
async def list_roles(
    page: Optional[int] = Query(None, ge=1, description="Page number"),
    page_size: int = Query(10, ge=1, le=100, description="Items per page"),
    search: Optional[str] = Query(None, description="Search role by name or description"),
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Union[PaginatedRoleResponse, List[RoleResponse]]:
    """Lists all roles with count of assigned users."""
    stmt = (
        select(Role, func.count(UserRole.user_id).label("user_count"))
        .outerjoin(UserRole, UserRole.role_id == Role.id)
        .group_by(Role.id)
        .order_by(Role.name.asc())
    )

    if search:
        s = f"%{search.strip()}%"
        stmt = stmt.where(or_(Role.name.ilike(s), Role.description.ilike(s)))

    if page is not None:
        count_stmt = select(func.count(Role.id))
        if search:
            s = f"%{search.strip()}%"
            count_stmt = count_stmt.where(or_(Role.name.ilike(s), Role.description.ilike(s)))
        total = (await db.execute(count_stmt)).scalar() or 0

        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        res = await db.execute(stmt)
        rows = res.all()

        items = [
            RoleResponse(
                id=role.id,
                name=role.name,
                description=role.description,
                is_system_role=role.is_system_role,
                user_count=ucount,
                created_at=role.created_at,
            )
            for role, ucount in rows
        ]
        total_pages = math.ceil(total / page_size) if total > 0 else 1
        return PaginatedRoleResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )

    res = await db.execute(stmt)
    rows = res.all()
    return [
        RoleResponse(
            id=role.id,
            name=role.name,
            description=role.description,
            is_system_role=role.is_system_role,
            user_count=ucount,
            created_at=role.created_at,
        )
        for role, ucount in rows
    ]


@router.post("", response_model=RoleResponse, status_code=status.HTTP_201_CREATED, summary="Create custom role")
async def create_role(
    payload: RoleCreateRequest,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> RoleResponse:
    if not user_ctx.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only SuperAdmin can create roles")

    # Check unique name
    stmt = select(Role).where(Role.name == payload.name)
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Role '{payload.name}' already exists")

    new_role = Role(
        id=uuid.uuid4(),
        name=payload.name,
        description=payload.description,
        is_system_role=False,
    )
    db.add(new_role)
    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user_id,
        action="CREATE_ROLE",
        entity_type=EntityType.DATABASE,
        entity_id=new_role.id,
        diff_jsonb={"name": new_role.name, "description": new_role.description},
        flush=True,
    )
    await db.flush()

    return RoleResponse(
        id=new_role.id,
        name=new_role.name,
        description=new_role.description,
        is_system_role=new_role.is_system_role,
        user_count=0,
        created_at=new_role.created_at,
    )


@router.get("/{role_id}", response_model=RoleResponse, summary="Get role details")
async def get_role(
    role_id: uuid.UUID,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> RoleResponse:
    role = await db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")

    uc_stmt = select(func.count(UserRole.user_id)).where(UserRole.role_id == role.id)
    uc = (await db.execute(uc_stmt)).scalar() or 0

    return RoleResponse(
        id=role.id,
        name=role.name,
        description=role.description,
        is_system_role=role.is_system_role,
        user_count=uc,
        created_at=role.created_at,
    )


@router.put("/{role_id}", response_model=RoleResponse, summary="Update role")
async def update_role(
    role_id: uuid.UUID,
    payload: RoleUpdateRequest,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> RoleResponse:
    if not user_ctx.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only SuperAdmin can update roles")

    role = await db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")

    old_name = role.name
    role.name = payload.name
    role.description = payload.description

    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user_id,
        action="UPDATE_ROLE",
        entity_type=EntityType.DATABASE,
        entity_id=role.id,
        diff_jsonb={"old_name": old_name, "new_name": role.name},
        flush=True,
    )
    await db.flush()

    uc_stmt = select(func.count(UserRole.user_id)).where(UserRole.role_id == role.id)
    uc = (await db.execute(uc_stmt)).scalar() or 0

    return RoleResponse(
        id=role.id,
        name=role.name,
        description=role.description,
        is_system_role=role.is_system_role,
        user_count=uc,
        created_at=role.created_at,
    )


@router.delete("/{role_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete custom role")
async def delete_role(
    role_id: uuid.UUID,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    if not user_ctx.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only SuperAdmin can delete roles")

    role = await db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")

    if role.is_system_role:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="System roles cannot be deleted")

    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user_id,
        action="DELETE_ROLE",
        entity_type=EntityType.DATABASE,
        entity_id=role.id,
        diff_jsonb={"deleted_role": role.name},
        flush=True,
    )
    await db.delete(role)
    await db.flush()


@router.get("/{role_id}/users", response_model=List[RoleUserItem], summary="Get users assigned to a role")
async def get_role_users(
    role_id: uuid.UUID,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> List[RoleUserItem]:
    role = await db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")

    items: List[RoleUserItem] = []

    # Direct assignments
    direct_stmt = (
        select(User)
        .join(UserRole, UserRole.user_id == User.id)
        .where(UserRole.role_id == role_id)
        .order_by(User.full_name.asc())
    )
    direct_users = (await db.execute(direct_stmt)).scalars().all()
    direct_user_ids = set()
    for u in direct_users:
        direct_user_ids.add(u.id)
        items.append(
            RoleUserItem(
                id=u.id,
                username=u.username,
                full_name=u.full_name,
                email=u.email,
                assignment_type="DIRECT",
            )
        )

    # Group assignments
    group_stmt = (
        select(User, GroupRole.group_id)
        .join(UserGroup, UserGroup.user_id == User.id)
        .join(GroupRole, GroupRole.group_id == UserGroup.group_id)
        .where(GroupRole.role_id == role_id)
    )
    group_users = (await db.execute(group_stmt)).all()
    for u, gid in group_users:
        if u.id not in direct_user_ids:
            items.append(
                RoleUserItem(
                    id=u.id,
                    username=u.username,
                    full_name=u.full_name,
                    email=u.email,
                    assignment_type="VIA_GROUP",
                )
            )

    return items


@router.get("/{role_id}/permissions", response_model=List[dict], summary="Get permissions granted to role")
async def get_role_permission_rules(
    role_id: uuid.UUID,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> List[dict]:
    role = await db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")

    stmt = select(PermissionRule).where(PermissionRule.role_id == role_id).order_by(PermissionRule.created_at.desc())
    rules = (await db.execute(stmt)).scalars().all()
    return [
        {
            "id": r.id,
            "role_id": r.role_id,
            "resource_type": r.resource_type.value,
            "resource_id": r.resource_id,
            "action": r.action.value,
            "effect": r.effect.value,
            "created_at": r.created_at,
        }
        for r in rules
    ]


@router.put("/{role_id}/permissions", response_model=List[dict], summary="Update permissions for role")
async def update_role_permissions(
    role_id: uuid.UUID,
    payload: RolePermissionsUpdateRequest,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> List[dict]:
    if not user_ctx.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only SuperAdmin can manage role permissions")

    role = await db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")

    # Clear existing permissions
    del_stmt = select(PermissionRule).where(PermissionRule.role_id == role_id)
    old_rules = (await db.execute(del_stmt)).scalars().all()
    for r in old_rules:
        await db.delete(r)
    await db.flush()

    # Add new permissions
    for item in payload.permissions:
        rule = PermissionRule(
            id=uuid.uuid4(),
            role_id=role_id,
            resource_type=item.resource_type,
            resource_id=item.resource_id,
            action=item.action,
            effect=item.effect,
        )
        db.add(rule)

    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user_id,
        action="UPDATE_ROLE_PERMISSIONS",
        entity_type=EntityType.DATABASE,
        entity_id=role_id,
        diff_jsonb={"role_name": role.name, "permissions_count": len(payload.permissions)},
        flush=True,
    )
    await db.flush()

    return await get_role_permission_rules(role_id=role_id, user_ctx=user_ctx, db=db)
