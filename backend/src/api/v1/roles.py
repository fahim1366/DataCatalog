from __future__ import annotations

import logging
from typing import List, Optional
import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.src.api.dependencies.rbac import UserContext, get_current_user
from backend.src.core.database import get_db_session
from backend.src.models.identity import Role, UserRole
from backend.src.models.rbac import EntityType
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


@router.get("", response_model=List[RoleResponse], summary="List all roles")
async def list_roles(
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> List[RoleResponse]:
    """Lists all roles with count of assigned users."""
    stmt = (
        select(Role, func.count(UserRole.user_id).label("user_count"))
        .outerjoin(UserRole, UserRole.role_id == Role.id)
        .group_by(Role.id)
        .order_by(Role.name.asc())
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
        actor_id=user_ctx.user.id,
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
        actor_id=user_ctx.user.id,
        action="UPDATE_ROLE",
        entity_type=EntityType.DATABASE,
        entity_id=role.id,
        diff_jsonb={"old_name": old_name, "new_name": role.name},
        flush=True,
    )
    await db.flush()

    # User count
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
        actor_id=user_ctx.user.id,
        action="DELETE_ROLE",
        entity_type=EntityType.DATABASE,
        entity_id=role.id,
        diff_jsonb={"deleted_role": role.name},
        flush=True,
    )
    await db.delete(role)
    await db.flush()
