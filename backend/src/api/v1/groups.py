from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime
from typing import List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.src.api.dependencies.rbac import UserContext, get_current_user
from backend.src.core.database import get_db_session
from backend.src.models.identity import Group, GroupRole, Role, User, UserGroup
from backend.src.models.rbac import (
    EntityType,
    PermissionAction,
    PermissionEffect,
    PermissionRule,
    ResourceType,
)
from backend.src.services.audit.audit_service import record_audit_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/groups", tags=["Groups"])


class GroupCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    description: Optional[str] = None
    is_active: bool = True
    role_ids: List[uuid.UUID] = Field(default_factory=list)
    user_ids: List[uuid.UUID] = Field(default_factory=list)


class GroupUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    description: Optional[str] = None
    is_active: Optional[bool] = None


class GroupRoleUpdateRequest(BaseModel):
    role_ids: List[uuid.UUID]


class GroupMembersUpdateRequest(BaseModel):
    user_ids: List[uuid.UUID]


class GroupMemberItem(BaseModel):
    id: uuid.UUID
    username: str
    full_name: str
    email: str
    status: str
    assigned_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class GroupResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: Optional[str] = None
    is_active: bool
    user_count: int = 0
    role_count: int = 0
    roles: List[str] = Field(default_factory=list)
    role_ids: List[uuid.UUID] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PaginatedGroupResponse(BaseModel):
    items: List[GroupResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class BulkGroupPermissionItem(BaseModel):
    resource_type: ResourceType
    resource_id: uuid.UUID
    action: PermissionAction
    effect: PermissionEffect = PermissionEffect.ALLOW


class BulkGroupPermissionsRequest(BaseModel):
    permissions: List[BulkGroupPermissionItem]


@router.get(
    "",
    response_model=Union[PaginatedGroupResponse, List[GroupResponse]],
    summary="List groups (supports pagination and search)",
)
async def list_groups(
    page: Optional[int] = Query(None, ge=1, description="Page number (1-based)"),
    page_size: int = Query(10, ge=1, le=100, description="Items per page"),
    search: Optional[str] = Query(None, description="Search by name or description"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Union[PaginatedGroupResponse, List[GroupResponse]]:
    stmt = (
        select(Group)
        .options(
            selectinload(Group.roles).selectinload(GroupRole.role),
            selectinload(Group.users),
        )
        .order_by(Group.name.asc())
    )

    if search:
        s = f"%{search.strip()}%"
        stmt = stmt.where(or_(Group.name.ilike(s), Group.description.ilike(s)))
    if is_active is not None:
        stmt = stmt.where(Group.is_active == is_active)

    if page is not None:
        # Count total
        count_stmt = select(func.count(Group.id))
        if search:
            s = f"%{search.strip()}%"
            count_stmt = count_stmt.where(or_(Group.name.ilike(s), Group.description.ilike(s)))
        if is_active is not None:
            count_stmt = count_stmt.where(Group.is_active == is_active)
        total = (await db.execute(count_stmt)).scalar() or 0

        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        res = await db.execute(stmt)
        groups = res.scalars().all()

        items = []
        for g in groups:
            r_names = [gr.role.name for gr in g.roles if gr.role]
            r_ids = [gr.role_id for gr in g.roles]
            items.append(
                GroupResponse(
                    id=g.id,
                    name=g.name,
                    description=g.description,
                    is_active=g.is_active,
                    user_count=len(g.users),
                    role_count=len(r_ids),
                    roles=r_names,
                    role_ids=r_ids,
                    created_at=g.created_at,
                    updated_at=g.updated_at,
                )
            )

        total_pages = math.ceil(total / page_size) if total > 0 else 1
        return PaginatedGroupResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )

    # Return list if page not requested
    res = await db.execute(stmt)
    groups = res.scalars().all()
    results = []
    for g in groups:
        r_names = [gr.role.name for gr in g.roles if gr.role]
        r_ids = [gr.role_id for gr in g.roles]
        results.append(
            GroupResponse(
                id=g.id,
                name=g.name,
                description=g.description,
                is_active=g.is_active,
                user_count=len(g.users),
                role_count=len(r_ids),
                roles=r_names,
                role_ids=r_ids,
                created_at=g.created_at,
                updated_at=g.updated_at,
            )
        )
    return results


@router.post(
    "",
    response_model=GroupResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user group",
)
async def create_group(
    payload: GroupCreateRequest,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> GroupResponse:
    if not user_ctx.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only SuperAdmin can create groups",
        )

    # Check unique name
    existing = (await db.execute(select(Group).where(Group.name == payload.name))).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Group with name '{payload.name}' already exists",
        )

    new_group = Group(
        id=uuid.uuid4(),
        name=payload.name,
        description=payload.description,
        is_active=payload.is_active,
    )
    db.add(new_group)
    await db.flush()

    # Assign roles
    assigned_roles = []
    for rid in payload.role_ids:
        role = await db.get(Role, rid)
        if role:
            gr = GroupRole(group_id=new_group.id, role_id=role.id, assigned_by=user_ctx.user_id)
            db.add(gr)
            assigned_roles.append(role.name)

    # Assign users
    user_count = 0
    for uid in payload.user_ids:
        u = await db.get(User, uid)
        if u:
            ug = UserGroup(group_id=new_group.id, user_id=u.id, assigned_by=user_ctx.user_id)
            db.add(ug)
            user_count += 1

    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user_id,
        action="CREATE_GROUP",
        entity_type=EntityType.DATABASE,
        entity_id=new_group.id,
        diff_jsonb={
            "name": new_group.name,
            "roles": assigned_roles,
            "user_count": user_count,
        },
        flush=True,
    )
    await db.flush()

    return GroupResponse(
        id=new_group.id,
        name=new_group.name,
        description=new_group.description,
        is_active=new_group.is_active,
        user_count=user_count,
        role_count=len(assigned_roles),
        roles=assigned_roles,
        role_ids=payload.role_ids,
        created_at=new_group.created_at,
        updated_at=new_group.updated_at,
    )


@router.get(
    "/{group_id}",
    response_model=GroupResponse,
    summary="Get group by ID",
)
async def get_group(
    group_id: uuid.UUID,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> GroupResponse:
    stmt = (
        select(Group)
        .options(
            selectinload(Group.roles).selectinload(GroupRole.role),
            selectinload(Group.users),
        )
        .where(Group.id == group_id)
    )
    res = await db.execute(stmt)
    g = res.scalar_one_or_none()
    if not g:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found",
        )

    r_names = [gr.role.name for gr in g.roles if gr.role]
    r_ids = [gr.role_id for gr in g.roles]

    return GroupResponse(
        id=g.id,
        name=g.name,
        description=g.description,
        is_active=g.is_active,
        user_count=len(g.users),
        role_count=len(r_ids),
        roles=r_names,
        role_ids=r_ids,
        created_at=g.created_at,
        updated_at=g.updated_at,
    )


@router.put(
    "/{group_id}",
    response_model=GroupResponse,
    summary="Update group details",
)
async def update_group(
    group_id: uuid.UUID,
    payload: GroupUpdateRequest,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> GroupResponse:
    if not user_ctx.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only SuperAdmin can update groups",
        )

    g = await db.get(Group, group_id)
    if not g:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found",
        )

    diff = {}
    if payload.name is not None and payload.name != g.name:
        existing = (await db.execute(select(Group).where(Group.name == payload.name, Group.id != group_id))).scalar_one_or_none()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Group with name '{payload.name}' already exists",
            )
        diff["old_name"] = g.name
        diff["new_name"] = payload.name
        g.name = payload.name

    if payload.description is not None:
        diff["description"] = payload.description
        g.description = payload.description

    if payload.is_active is not None:
        diff["is_active"] = payload.is_active
        g.is_active = payload.is_active

    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user_id,
        action="UPDATE_GROUP",
        entity_type=EntityType.DATABASE,
        entity_id=g.id,
        diff_jsonb=diff,
        flush=True,
    )
    await db.flush()

    return await get_group(group_id=group_id, user_ctx=user_ctx, db=db)


@router.delete(
    "/{group_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete group",
)
async def delete_group(
    group_id: uuid.UUID,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    if not user_ctx.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only SuperAdmin can delete groups",
        )

    g = await db.get(Group, group_id)
    if not g:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found",
        )

    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user_id,
        action="DELETE_GROUP",
        entity_type=EntityType.DATABASE,
        entity_id=g.id,
        diff_jsonb={"deleted_group": g.name},
        flush=True,
    )
    await db.delete(g)
    await db.flush()


@router.get(
    "/{group_id}/members",
    response_model=List[GroupMemberItem],
    summary="List members of a group",
)
async def get_group_members(
    group_id: uuid.UUID,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> List[GroupMemberItem]:
    g = await db.get(Group, group_id)
    if not g:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found",
        )

    stmt = (
        select(User, UserGroup.assigned_at)
        .join(UserGroup, UserGroup.user_id == User.id)
        .where(UserGroup.group_id == group_id)
        .order_by(User.full_name.asc())
    )
    res = await db.execute(stmt)
    rows = res.all()

    return [
        GroupMemberItem(
            id=u.id,
            username=u.username,
            full_name=u.full_name,
            email=u.email,
            status=u.status.value,
            assigned_at=assigned_at,
        )
        for u, assigned_at in rows
    ]


@router.put(
    "/{group_id}/members",
    response_model=List[GroupMemberItem],
    summary="Bulk update group members",
)
async def update_group_members(
    group_id: uuid.UUID,
    payload: GroupMembersUpdateRequest,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> List[GroupMemberItem]:
    if not user_ctx.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only SuperAdmin can manage group members",
        )

    g = await db.get(Group, group_id)
    if not g:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found",
        )

    # Delete existing memberships
    del_stmt = select(UserGroup).where(UserGroup.group_id == group_id)
    existing_members = (await db.execute(del_stmt)).scalars().all()
    for em in existing_members:
        await db.delete(em)
    await db.flush()

    # Add new memberships
    for uid in payload.user_ids:
        u = await db.get(User, uid)
        if u:
            ug = UserGroup(group_id=group_id, user_id=uid, assigned_by=user_ctx.user_id)
            db.add(ug)

    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user_id,
        action="UPDATE_GROUP_MEMBERS",
        entity_type=EntityType.DATABASE,
        entity_id=group_id,
        diff_jsonb={"group_name": g.name, "member_count": len(payload.user_ids)},
        flush=True,
    )
    await db.flush()

    return await get_group_members(group_id=group_id, user_ctx=user_ctx, db=db)


@router.post(
    "/{group_id}/members/{user_id}",
    status_code=status.HTTP_201_CREATED,
    summary="Add user to group",
)
async def add_group_member(
    group_id: uuid.UUID,
    user_id: uuid.UUID,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    if not user_ctx.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only SuperAdmin can manage group members",
        )

    g = await db.get(Group, group_id)
    if not g:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    u = await db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    existing = (await db.execute(select(UserGroup).where(UserGroup.group_id == group_id, UserGroup.user_id == user_id))).scalar_one_or_none()
    if not existing:
        ug = UserGroup(group_id=group_id, user_id=user_id, assigned_by=user_ctx.user_id)
        db.add(ug)
        await record_audit_event(
            session=db,
            actor_username=user_ctx.user.username,
            actor_id=user_ctx.user_id,
            action="ADD_GROUP_MEMBER",
            entity_type=EntityType.DATABASE,
            entity_id=group_id,
            diff_jsonb={"group_name": g.name, "username": u.username},
            flush=True,
        )
        await db.flush()


@router.delete(
    "/{group_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove user from group",
)
async def remove_group_member(
    group_id: uuid.UUID,
    user_id: uuid.UUID,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    if not user_ctx.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only SuperAdmin can manage group members",
        )

    g = await db.get(Group, group_id)
    if not g:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    u = await db.get(User, user_id)

    ug = (await db.execute(select(UserGroup).where(UserGroup.group_id == group_id, UserGroup.user_id == user_id))).scalar_one_or_none()
    if ug:
        await db.delete(ug)
        await record_audit_event(
            session=db,
            actor_username=user_ctx.user.username,
            actor_id=user_ctx.user_id,
            action="REMOVE_GROUP_MEMBER",
            entity_type=EntityType.DATABASE,
            entity_id=group_id,
            diff_jsonb={"group_name": g.name, "user_id": str(user_id), "username": u.username if u else None},
            flush=True,
        )
        await db.flush()


@router.get(
    "/{group_id}/roles",
    response_model=List[dict],
    summary="Get roles assigned to group",
)
async def get_group_roles(
    group_id: uuid.UUID,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> List[dict]:
    g = await db.get(Group, group_id)
    if not g:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    stmt = (
        select(Role, GroupRole.assigned_at)
        .join(GroupRole, GroupRole.role_id == Role.id)
        .where(GroupRole.group_id == group_id)
        .order_by(Role.name.asc())
    )
    res = await db.execute(stmt)
    rows = res.all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "description": r.description,
            "is_system_role": r.is_system_role,
            "assigned_at": assigned_at,
        }
        for r, assigned_at in rows
    ]


@router.put(
    "/{group_id}/roles",
    response_model=List[dict],
    summary="Bulk update group roles",
)
async def update_group_roles(
    group_id: uuid.UUID,
    payload: GroupRoleUpdateRequest,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> List[dict]:
    if not user_ctx.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only SuperAdmin can manage group roles",
        )

    g = await db.get(Group, group_id)
    if not g:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    # Remove existing group roles
    del_stmt = select(GroupRole).where(GroupRole.group_id == group_id)
    existing = (await db.execute(del_stmt)).scalars().all()
    for e in existing:
        await db.delete(e)
    await db.flush()

    # Assign new roles
    assigned_role_names = []
    for rid in payload.role_ids:
        role = await db.get(Role, rid)
        if role:
            gr = GroupRole(group_id=group_id, role_id=role.id, assigned_by=user_ctx.user_id)
            db.add(gr)
            assigned_role_names.append(role.name)

    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user_id,
        action="UPDATE_GROUP_ROLES",
        entity_type=EntityType.DATABASE,
        entity_id=group_id,
        diff_jsonb={"group_name": g.name, "roles": assigned_role_names},
        flush=True,
    )
    await db.flush()

    return await get_group_roles(group_id=group_id, user_ctx=user_ctx, db=db)


@router.get(
    "/{group_id}/permissions",
    response_model=List[dict],
    summary="Get direct permission rules for group",
)
async def get_group_permissions(
    group_id: uuid.UUID,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> List[dict]:
    stmt = (
        select(PermissionRule)
        .where(PermissionRule.group_id == group_id)
        .order_by(PermissionRule.created_at.desc())
    )
    res = await db.execute(stmt)
    rules = res.scalars().all()
    return [
        {
            "id": r.id,
            "group_id": r.group_id,
            "resource_type": r.resource_type.value,
            "resource_id": r.resource_id,
            "action": r.action.value,
            "effect": r.effect.value,
            "created_at": r.created_at,
        }
        for r in rules
    ]


@router.post(
    "/{group_id}/permissions/bulk",
    response_model=List[dict],
    summary="Bulk update direct permissions for group",
)
async def bulk_update_group_permissions(
    group_id: uuid.UUID,
    payload: BulkGroupPermissionsRequest,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> List[dict]:
    if not user_ctx.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only SuperAdmin can manage group permissions",
        )

    g = await db.get(Group, group_id)
    if not g:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    # Clear existing direct resource permissions for this group
    del_stmt = select(PermissionRule).where(
        PermissionRule.group_id == group_id,
        PermissionRule.resource_type.in_([ResourceType.DATABASE, ResourceType.SCHEMA, ResourceType.TABLE]),
    )
    old_rules = (await db.execute(del_stmt)).scalars().all()
    for rule in old_rules:
        await db.delete(rule)
    await db.flush()

    created_rules = []
    for item in payload.permissions:
        rule = PermissionRule(
            id=uuid.uuid4(),
            group_id=group_id,
            resource_type=item.resource_type,
            resource_id=item.resource_id,
            action=item.action,
            effect=item.effect,
        )
        db.add(rule)
        created_rules.append(rule)

    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user_id,
        action="UPDATE_GROUP_PERMISSIONS",
        entity_type=EntityType.DATABASE,
        entity_id=group_id,
        diff_jsonb={"group_name": g.name, "rules_count": len(payload.permissions)},
        flush=True,
    )
    await db.flush()

    return await get_group_permissions(group_id=group_id, user_ctx=user_ctx, db=db)
