from __future__ import annotations

import logging
from typing import List, Optional
import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.src.api.dependencies.rbac import UserContext, get_current_user
from backend.src.core.database import get_db_session
from backend.src.models.identity import Role, User, UserRole, UserStatus
from backend.src.models.rbac import EntityType
from backend.src.services.audit.audit_service import record_audit_event
from backend.src.services.auth.jwt_service import auth_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["Users"])


class UserCreateRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: str = Field(..., min_length=5, max_length=255)
    full_name: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=8)
    is_superadmin: bool = False
    role_ids: List[uuid.UUID] = Field(default_factory=list)


class UserUpdateRequest(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    status: Optional[UserStatus] = None
    is_superadmin: Optional[bool] = None


class UserStatusUpdateRequest(BaseModel):
    status: UserStatus


class UserRolesUpdateRequest(BaseModel):
    role_ids: List[uuid.UUID]


class UserResponse(BaseModel):
    id: uuid.UUID
    username: str
    email: str
    full_name: str
    status: str
    is_superadmin: bool
    roles: List[str] = Field(default_factory=list)
    role_ids: List[uuid.UUID] = Field(default_factory=list)
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


@router.get("", response_model=List[UserResponse], summary="List all platform users")
async def list_users(
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> List[UserResponse]:
    """Lists all users with their assigned roles."""
    stmt = (
        select(User)
        .options(
            selectinload(User.roles).selectinload(UserRole.role)
        )
        .order_by(User.created_at.desc())
    )
    result = await db.execute(stmt)
    users = result.scalars().all()

    resp = []
    for u in users:
        role_names = [ur.role.name for ur in u.roles if ur.role]
        r_ids = [ur.role_id for ur in u.roles]
        resp.append(
            UserResponse(
                id=u.id,
                username=u.username,
                email=u.email,
                full_name=u.full_name,
                status=u.status.value,
                is_superadmin=u.is_superadmin,
                roles=role_names,
                role_ids=r_ids,
                created_at=u.created_at,
            )
        )
    return resp


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

    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user_id,
        action="CREATE_USER",
        entity_type=EntityType.DATABASE,
        entity_id=new_user.id,
        diff_jsonb={"username": new_user.username, "email": new_user.email, "roles": assigned_roles},
        flush=True,
    )
    await db.flush()

    return UserResponse(
        id=new_user.id,
        username=new_user.username,
        email=new_user.email,
        full_name=new_user.full_name,
        status=new_user.status.value,
        is_superadmin=new_user.is_superadmin,
        roles=assigned_roles,
        role_ids=payload.role_ids,
        created_at=new_user.created_at,
    )


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

    roles = await auth_service.get_user_roles(db, user.id)
    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        status=user.status.value,
        is_superadmin=user.is_superadmin,
        roles=roles,
        role_ids=[],
        created_at=user.created_at,
    )


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

    # Remove current roles
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

    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        status=user.status.value,
        is_superadmin=user.is_superadmin,
        roles=assigned_role_names,
        role_ids=payload.role_ids,
        created_at=user.created_at,
    )


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
