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
from backend.src.models.audit import AuditLog
from backend.src.models.identity import Group, Role, User, UserGroup, UserRole, UserStatus
from backend.src.models.rbac import (
    EntityType,
    PermissionAction,
    PermissionEffect,
    PermissionRule,
    ResourceType,
)
from backend.src.services.audit.audit_service import record_audit_event
from backend.src.services.auth.jwt_service import auth_service
from backend.src.services.rbac.effective_permission_service import (
    EffectivePermissionRow,
    EffectivePermissionService,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["Users"])


class UserCreateRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: str = Field(..., min_length=5, max_length=255)
    full_name: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=8)
    phone: Optional[str] = Field(None, max_length=50)
    is_superadmin: bool = False
    role_ids: List[uuid.UUID] = Field(default_factory=list)
    group_ids: List[uuid.UUID] = Field(default_factory=list)


class UserUpdateRequest(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    status: Optional[UserStatus] = None
    is_superadmin: Optional[bool] = None
    role_ids: Optional[List[uuid.UUID]] = None
    group_ids: Optional[List[uuid.UUID]] = None


class UserStatusUpdateRequest(BaseModel):
    status: UserStatus


class UserRolesUpdateRequest(BaseModel):
    role_ids: List[uuid.UUID]


class UserGroupsUpdateRequest(BaseModel):
    group_ids: List[uuid.UUID]


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(..., min_length=8, description="Minimum 8 characters")


class UserResponse(BaseModel):
    id: uuid.UUID
    username: str
    email: str
    full_name: str
    phone: Optional[str] = None
    status: str
    is_superadmin: bool
    roles: List[str] = Field(default_factory=list)
    role_ids: List[uuid.UUID] = Field(default_factory=list)
    groups: List[str] = Field(default_factory=list)
    group_ids: List[uuid.UUID] = Field(default_factory=list)
    last_login_at: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PaginatedUserResponse(BaseModel):
    items: List[UserResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class UserDetailResponse(BaseModel):
    user: UserResponse
    roles_detail: List[Dict[str, Any]]
    groups_detail: List[Dict[str, Any]]
    direct_permissions_count: int
    activity_count: int


class BulkUserPermissionItem(BaseModel):
    resource_type: ResourceType
    resource_id: uuid.UUID
    action: PermissionAction
    effect: PermissionEffect = PermissionEffect.ALLOW


class BulkUserPermissionsRequest(BaseModel):
    permissions: List[BulkUserPermissionItem]


class UserActivityItem(BaseModel):
    id: uuid.UUID
    actor_username: str
    action: str
    entity_type: str
    entity_id: uuid.UUID
    diff_jsonb: Dict[str, Any] = Field(default_factory=dict)
    ip_address: Optional[str] = None
    created_at: datetime


@router.get(
    "",
    response_model=Union[PaginatedUserResponse, List[UserResponse]],
    summary="List all platform users (supports pagination, search, and filters)",
)
async def list_users(
    page: Optional[int] = Query(None, ge=1, description="Page number"),
    page_size: int = Query(10, ge=1, le=100, description="Items per page"),
    search: Optional[str] = Query(None, description="Search by name, username, or email"),
    role_id: Optional[uuid.UUID] = Query(None, description="Filter by role ID"),
    group_id: Optional[uuid.UUID] = Query(None, description="Filter by group ID"),
    status: Optional[UserStatus] = Query(None, description="Filter by status (ACTIVE, SUSPENDED, DEACTIVATED)"),
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Union[PaginatedUserResponse, List[UserResponse]]:
    stmt = (
        select(User)
        .options(
            selectinload(User.roles).selectinload(UserRole.role),
            selectinload(User.groups).selectinload(UserGroup.group),
        )
        .order_by(User.created_at.desc())
    )

    if search:
        s = f"%{search.strip()}%"
        stmt = stmt.where(or_(User.username.ilike(s), User.full_name.ilike(s), User.email.ilike(s)))
    if status is not None:
        stmt = stmt.where(User.status == status)
    if role_id is not None:
        stmt = stmt.join(UserRole, UserRole.user_id == User.id).where(UserRole.role_id == role_id)
    if group_id is not None:
        stmt = stmt.join(UserGroup, UserGroup.user_id == User.id).where(UserGroup.group_id == group_id)

    if page is not None:
        count_stmt = select(func.count(func.distinct(User.id)))
        if search:
            s = f"%{search.strip()}%"
            count_stmt = count_stmt.where(or_(User.username.ilike(s), User.full_name.ilike(s), User.email.ilike(s)))
        if status is not None:
            count_stmt = count_stmt.where(User.status == status)
        if role_id is not None:
            count_stmt = count_stmt.join(UserRole, UserRole.user_id == User.id).where(UserRole.role_id == role_id)
        if group_id is not None:
            count_stmt = count_stmt.join(UserGroup, UserGroup.user_id == User.id).where(UserGroup.group_id == group_id)

        total = (await db.execute(count_stmt)).scalar() or 0

        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        res = await db.execute(stmt)
        users = res.scalars().all()

        items = []
        for u in users:
            r_names = [ur.role.name for ur in u.roles if ur.role]
            r_ids = [ur.role_id for ur in u.roles]
            g_names = [ug.group.name for ug in u.groups if ug.group]
            g_ids = [ug.group_id for ug in u.groups]
            items.append(
                UserResponse(
                    id=u.id,
                    username=u.username,
                    email=u.email,
                    full_name=u.full_name,
                    phone=u.phone,
                    status=u.status.value,
                    is_superadmin=u.is_superadmin,
                    roles=r_names,
                    role_ids=r_ids,
                    groups=g_names,
                    group_ids=g_ids,
                    last_login_at=u.last_login_at,
                    created_at=u.created_at,
                )
            )

        total_pages = math.ceil(total / page_size) if total > 0 else 1
        return PaginatedUserResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )

    # Return list if page not provided
    res = await db.execute(stmt)
    users = res.scalars().all()
    results = []
    for u in users:
        r_names = [ur.role.name for ur in u.roles if ur.role]
        r_ids = [ur.role_id for ur in u.roles]
        g_names = [ug.group.name for ug in u.groups if ug.group]
        g_ids = [ug.group_id for ug in u.groups]
        results.append(
            UserResponse(
                id=u.id,
                username=u.username,
                email=u.email,
                full_name=u.full_name,
                phone=u.phone,
                status=u.status.value,
                is_superadmin=u.is_superadmin,
                roles=r_names,
                role_ids=r_ids,
                groups=g_names,
                group_ids=g_ids,
                last_login_at=u.last_login_at,
                created_at=u.created_at,
            )
        )
    return results


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED, summary="Create a new user")
async def create_user(
    payload: UserCreateRequest,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> UserResponse:
    if not user_ctx.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only SuperAdmin can manage users")

    # Check unique username or email
    stmt = select(User).where((User.username == payload.username) | (User.email == payload.email))
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User with this username or email already exists",
        )

    new_user = User(
        id=uuid.uuid4(),
        username=payload.username,
        email=payload.email,
        full_name=payload.full_name,
        phone=payload.phone,
        hashed_password=auth_service.hash_password(payload.password),
        status=UserStatus.ACTIVE,
        is_superadmin=payload.is_superadmin,
    )
    db.add(new_user)
    await db.flush()

    assigned_roles = []
    for rid in payload.role_ids:
        role = await db.get(Role, rid)
        if role:
            ur = UserRole(
                user_id=new_user.id,
                role_id=role.id,
                assigned_by=user_ctx.user_id,
            )
            db.add(ur)
            assigned_roles.append(role.name)

    assigned_groups = []
    for gid in payload.group_ids:
        grp = await db.get(Group, gid)
        if grp:
            ug = UserGroup(
                user_id=new_user.id,
                group_id=grp.id,
                assigned_by=user_ctx.user_id,
            )
            db.add(ug)
            assigned_groups.append(grp.name)

    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user_id,
        action="CREATE_USER",
        entity_type=EntityType.DATABASE,
        entity_id=new_user.id,
        diff_jsonb={
            "username": new_user.username,
            "email": new_user.email,
            "full_name": new_user.full_name,
            "phone": new_user.phone,
            "roles": assigned_roles,
            "groups": assigned_groups,
        },
        flush=True,
    )
    await db.flush()

    return UserResponse(
        id=new_user.id,
        username=new_user.username,
        email=new_user.email,
        full_name=new_user.full_name,
        phone=new_user.phone,
        status=new_user.status.value,
        is_superadmin=new_user.is_superadmin,
        roles=assigned_roles,
        role_ids=payload.role_ids,
        groups=assigned_groups,
        group_ids=payload.group_ids,
        last_login_at=new_user.last_login_at,
        created_at=new_user.created_at,
    )


@router.get("/{user_id}", response_model=UserResponse, summary="Get user by ID")
async def get_user_by_id(
    user_id: uuid.UUID,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> UserResponse:
    stmt = (
        select(User)
        .options(
            selectinload(User.roles).selectinload(UserRole.role),
            selectinload(User.groups).selectinload(UserGroup.group),
        )
        .where(User.id == user_id)
    )
    u = (await db.execute(stmt)).scalar_one_or_none()
    if not u:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    r_names = [ur.role.name for ur in u.roles if ur.role]
    r_ids = [ur.role_id for ur in u.roles]
    g_names = [ug.group.name for ug in u.groups if ug.group]
    g_ids = [ug.group_id for ug in u.groups]

    return UserResponse(
        id=u.id,
        username=u.username,
        email=u.email,
        full_name=u.full_name,
        phone=u.phone,
        status=u.status.value,
        is_superadmin=u.is_superadmin,
        roles=r_names,
        role_ids=r_ids,
        groups=g_names,
        group_ids=g_ids,
        last_login_at=u.last_login_at,
        created_at=u.created_at,
    )


@router.get("/{user_id}/details", response_model=UserDetailResponse, summary="Get full user profile & stats")
async def get_user_details(
    user_id: uuid.UUID,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> UserDetailResponse:
    u_resp = await get_user_by_id(user_id=user_id, user_ctx=user_ctx, db=db)

    # Detailed roles
    r_stmt = (
        select(Role, UserRole.assigned_at)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user_id)
    )
    r_rows = (await db.execute(r_stmt)).all()
    roles_detail = [
        {"id": r.id, "name": r.name, "description": r.description, "assigned_at": assigned_at}
        for r, assigned_at in r_rows
    ]

    # Detailed groups
    g_stmt = (
        select(Group, UserGroup.assigned_at)
        .join(UserGroup, UserGroup.group_id == Group.id)
        .where(UserGroup.user_id == user_id)
    )
    g_rows = (await db.execute(g_stmt)).all()
    groups_detail = [
        {"id": g.id, "name": g.name, "description": g.description, "assigned_at": assigned_at}
        for g, assigned_at in g_rows
    ]

    # Direct permissions count
    p_cnt_stmt = select(func.count(PermissionRule.id)).where(PermissionRule.user_id == user_id)
    p_cnt = (await db.execute(p_cnt_stmt)).scalar() or 0

    # Activity count
    act_cnt_stmt = select(func.count(AuditLog.id)).where(
        or_(
            AuditLog.actor_id == user_id,
            (AuditLog.entity_type == EntityType.DATABASE) & (AuditLog.entity_id == user_id),
        )
    )
    act_cnt = (await db.execute(act_cnt_stmt)).scalar() or 0

    return UserDetailResponse(
        user=u_resp,
        roles_detail=roles_detail,
        groups_detail=groups_detail,
        direct_permissions_count=p_cnt,
        activity_count=act_cnt,
    )


@router.put("/{user_id}", response_model=UserResponse, summary="Update user details, roles and groups")
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdateRequest,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> UserResponse:
    if not user_ctx.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only SuperAdmin can manage users")

    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    diff = {}
    if payload.full_name is not None and payload.full_name != user.full_name:
        diff["full_name"] = {"old": user.full_name, "new": payload.full_name}
        user.full_name = payload.full_name

    if payload.email is not None and payload.email != user.email:
        exist = (await db.execute(select(User).where(User.email == payload.email, User.id != user_id))).scalar_one_or_none()
        if exist:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already in use")
        diff["email"] = {"old": user.email, "new": payload.email}
        user.email = payload.email

    if payload.phone is not None:
        diff["phone"] = {"old": user.phone, "new": payload.phone}
        user.phone = payload.phone

    if payload.status is not None:
        diff["status"] = {"old": user.status.value, "new": payload.status.value}
        user.status = payload.status

    if payload.is_superadmin is not None:
        diff["is_superadmin"] = {"old": user.is_superadmin, "new": payload.is_superadmin}
        user.is_superadmin = payload.is_superadmin

    if payload.role_ids is not None:
        del_r = select(UserRole).where(UserRole.user_id == user.id)
        for old_r in (await db.execute(del_r)).scalars().all():
            await db.delete(old_r)
        await db.flush()
        assigned_roles = []
        for rid in payload.role_ids:
            role = await db.get(Role, rid)
            if role:
                db.add(UserRole(user_id=user.id, role_id=role.id, assigned_by=user_ctx.user_id))
                assigned_roles.append(role.name)
        diff["roles"] = assigned_roles

    if payload.group_ids is not None:
        del_g = select(UserGroup).where(UserGroup.user_id == user.id)
        for old_g in (await db.execute(del_g)).scalars().all():
            await db.delete(old_g)
        await db.flush()
        assigned_groups = []
        for gid in payload.group_ids:
            grp = await db.get(Group, gid)
            if grp:
                db.add(UserGroup(user_id=user.id, group_id=grp.id, assigned_by=user_ctx.user_id))
                assigned_groups.append(grp.name)
        diff["groups"] = assigned_groups

    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user_id,
        action="UPDATE_USER",
        entity_type=EntityType.DATABASE,
        entity_id=user.id,
        diff_jsonb=diff,
        flush=True,
    )
    await db.flush()

    return await get_user_by_id(user_id=user_id, user_ctx=user_ctx, db=db)


@router.post("/{user_id}/reset-password", status_code=status.HTTP_200_OK, summary="Reset user password")
async def reset_password(
    user_id: uuid.UUID,
    payload: ResetPasswordRequest,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, str]:
    if not user_ctx.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only SuperAdmin can reset passwords")

    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    user.hashed_password = auth_service.hash_password(payload.new_password)

    # Secure audit event: NEVER log passwords!
    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user_id,
        action="RESET_PASSWORD",
        entity_type=EntityType.DATABASE,
        entity_id=user.id,
        diff_jsonb={"target_username": user.username, "reset_by": user_ctx.user.username},
        flush=True,
    )
    await db.flush()

    return {"message": "کلمه عبور کاربر با موفقیت بازنشانی شد."}


@router.patch("/{user_id}/status", response_model=UserResponse, summary="Toggle user active status")
async def update_user_status(
    user_id: uuid.UUID,
    payload: UserStatusUpdateRequest,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> UserResponse:
    if not user_ctx.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only SuperAdmin can change user status")

    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    old_status = user.status.value
    user.status = payload.status

    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user_id,
        action="UPDATE_USER_STATUS",
        entity_type=EntityType.DATABASE,
        entity_id=user.id,
        diff_jsonb={"old_status": old_status, "new_status": user.status.value},
        flush=True,
    )
    await db.flush()

    return await get_user_by_id(user_id=user_id, user_ctx=user_ctx, db=db)


@router.put("/{user_id}/roles", response_model=UserResponse, summary="Assign or update roles for a user")
async def update_user_roles(
    user_id: uuid.UUID,
    payload: UserRolesUpdateRequest,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> UserResponse:
    if not user_ctx.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only SuperAdmin can manage user roles")

    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    del_stmt = select(UserRole).where(UserRole.user_id == user.id)
    current_roles = (await db.execute(del_stmt)).scalars().all()
    for cr in current_roles:
        await db.delete(cr)
    await db.flush()

    assigned_role_names = []
    for rid in payload.role_ids:
        role = await db.get(Role, rid)
        if role:
            ur = UserRole(
                user_id=user.id,
                role_id=role.id,
                assigned_by=user_ctx.user_id,
            )
            db.add(ur)
            assigned_role_names.append(role.name)

    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user_id,
        action="UPDATE_USER_ROLES",
        entity_type=EntityType.DATABASE,
        entity_id=user.id,
        diff_jsonb={"new_roles": assigned_role_names},
        flush=True,
    )
    await db.flush()

    return await get_user_by_id(user_id=user_id, user_ctx=user_ctx, db=db)


@router.get("/{user_id}/groups", response_model=List[Dict[str, Any]], summary="Get user groups")
async def get_user_groups(
    user_id: uuid.UUID,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> List[Dict[str, Any]]:
    stmt = (
        select(Group, UserGroup.assigned_at)
        .join(UserGroup, UserGroup.group_id == Group.id)
        .where(UserGroup.user_id == user_id)
        .order_by(Group.name.asc())
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "id": g.id,
            "name": g.name,
            "description": g.description,
            "is_active": g.is_active,
            "assigned_at": assigned_at,
        }
        for g, assigned_at in rows
    ]


@router.put("/{user_id}/groups", response_model=List[Dict[str, Any]], summary="Update user groups")
async def update_user_groups(
    user_id: uuid.UUID,
    payload: UserGroupsUpdateRequest,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> List[Dict[str, Any]]:
    if not user_ctx.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only SuperAdmin can manage user groups")

    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    del_g = select(UserGroup).where(UserGroup.user_id == user.id)
    for old_g in (await db.execute(del_g)).scalars().all():
        await db.delete(old_g)
    await db.flush()

    assigned_group_names = []
    for gid in payload.group_ids:
        grp = await db.get(Group, gid)
        if grp:
            db.add(UserGroup(user_id=user.id, group_id=grp.id, assigned_by=user_ctx.user_id))
            assigned_group_names.append(grp.name)

    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user_id,
        action="UPDATE_USER_GROUPS",
        entity_type=EntityType.DATABASE,
        entity_id=user.id,
        diff_jsonb={"groups": assigned_group_names},
        flush=True,
    )
    await db.flush()

    return await get_user_groups(user_id=user_id, user_ctx=user_ctx, db=db)


@router.get("/{user_id}/permissions", response_model=List[Dict[str, Any]], summary="Get direct user permissions")
async def get_user_direct_permissions(
    user_id: uuid.UUID,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> List[Dict[str, Any]]:
    stmt = (
        select(PermissionRule)
        .where(PermissionRule.user_id == user_id)
        .order_by(PermissionRule.created_at.desc())
    )
    rules = (await db.execute(stmt)).scalars().all()
    return [
        {
            "id": r.id,
            "user_id": r.user_id,
            "resource_type": r.resource_type.value,
            "resource_id": r.resource_id,
            "action": r.action.value,
            "effect": r.effect.value,
            "created_at": r.created_at,
        }
        for r in rules
    ]


@router.post("/{user_id}/permissions/bulk", response_model=List[Dict[str, Any]], summary="Bulk set direct user permissions")
async def bulk_update_user_permissions(
    user_id: uuid.UUID,
    payload: BulkUserPermissionsRequest,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> List[Dict[str, Any]]:
    if not user_ctx.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only SuperAdmin can manage user permissions")

    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Clear existing direct resource permissions for database, schema, table
    del_stmt = select(PermissionRule).where(
        PermissionRule.user_id == user_id,
        PermissionRule.resource_type.in_([ResourceType.DATABASE, ResourceType.SCHEMA, ResourceType.TABLE]),
    )
    old_rules = (await db.execute(del_stmt)).scalars().all()
    for rule in old_rules:
        await db.delete(rule)
    await db.flush()

    for item in payload.permissions:
        rule = PermissionRule(
            id=uuid.uuid4(),
            user_id=user_id,
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
        action="UPDATE_USER_PERMISSIONS",
        entity_type=EntityType.DATABASE,
        entity_id=user_id,
        diff_jsonb={"username": user.username, "rules_count": len(payload.permissions)},
        flush=True,
    )
    await db.flush()

    return await get_user_direct_permissions(user_id=user_id, user_ctx=user_ctx, db=db)


@router.delete("/{user_id}/permissions/{rule_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete single direct permission")
async def delete_user_permission_rule(
    user_id: uuid.UUID,
    rule_id: uuid.UUID,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    if not user_ctx.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only SuperAdmin can manage permissions")

    rule = await db.get(PermissionRule, rule_id)
    if not rule or rule.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Permission rule not found")

    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user_id,
        action="REVOKE_USER_PERMISSION",
        entity_type=EntityType.DATABASE,
        entity_id=user_id,
        diff_jsonb={"rule_id": str(rule.id), "resource_type": rule.resource_type.value, "action": rule.action.value},
        flush=True,
    )
    await db.delete(rule)
    await db.flush()


@router.get(
    "/{user_id}/effective-permissions",
    response_model=List[EffectivePermissionRow],
    summary="Get explainable effective permissions for user across Database -> Schema -> Table",
)
async def get_user_effective_permissions(
    user_id: uuid.UUID,
    search: Optional[str] = Query(None, description="Filter by resource name"),
    action: Optional[PermissionAction] = Query(None, description="Filter by action"),
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> List[EffectivePermissionRow]:
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return await EffectivePermissionService.get_user_effective_permissions(
        session=db,
        user_id=user_id,
        resource_query=search,
        permission_action=action,
    )


@router.get(
    "/{user_id}/activity",
    response_model=List[UserActivityItem],
    summary="Get user activity audit history",
)
async def get_user_activity(
    user_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> List[UserActivityItem]:
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    stmt = (
        select(AuditLog)
        .where(
            or_(
                AuditLog.actor_id == user_id,
                (AuditLog.entity_type == EntityType.DATABASE) & (AuditLog.entity_id == user_id),
            )
        )
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    )
    logs = (await db.execute(stmt)).scalars().all()

    return [
        UserActivityItem(
            id=log.id,
            actor_username=log.actor_username,
            action=log.action,
            entity_type=log.entity_type.value if hasattr(log.entity_type, "value") else str(log.entity_type),
            entity_id=log.entity_id,
            diff_jsonb=log.diff_jsonb or {},
            ip_address=str(log.ip_address) if log.ip_address else None,
            created_at=log.created_at,
        )
        for log in logs
    ]


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete user")
async def delete_user(
    user_id: uuid.UUID,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    if not user_ctx.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only SuperAdmin can delete users")

    if user_id == user_ctx.user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete current logged-in user")

    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user_id,
        action="DELETE_USER",
        entity_type=EntityType.DATABASE,
        entity_id=user.id,
        diff_jsonb={"deleted_username": user.username},
        flush=True,
    )
    await db.delete(user)
    await db.flush()
