from __future__ import annotations

import logging
from typing import List, Optional
import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.api.dependencies.rbac import UserContext, get_current_user
from backend.src.core.database import get_db_session
from backend.src.models.identity import Role, User, UserRole
from backend.src.models.rbac import (
    PermissionAction,
    PermissionEffect,
    PermissionRule,
    ResourceType,
)
from backend.src.services.auth.jwt_service import (
    InactiveUserError,
    InvalidCredentialsError,
    auth_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])


class LoginRequest(BaseModel):
    username_or_email: Optional[str] = Field(None, description="Username or email address")
    username: Optional[str] = Field(None, description="Username alternative")
    email: Optional[str] = Field(None, description="Email alternative")
    password: str = Field(..., min_length=1, description="Account password")


class AuthUserContext(BaseModel):
    id: str
    username: str
    email: str
    roles: List[str]
    is_superadmin: bool
    permissions: List[str] = Field(default_factory=list)


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: AuthUserContext


async def resolve_effective_permissions(
    session: AsyncSession,
    user: User,
    role_ids: List[uuid.UUID],
    role_names: List[str],
) -> List[str]:
    """
    Computes effective uppercase permission action strings for an authenticated user.
    SuperAdmin receives all permissions unconditionally.
    """
    if user.is_superadmin:
        return [action.value for action in PermissionAction]

    perms = set()

    # Apply standard role defaults
    for r in role_names:
        norm = r.upper().replace("_", "").replace("-", "")
        if "STEWARD" in norm:
            perms.update([
                PermissionAction.VIEW_METADATA.value,
                PermissionAction.EDIT_DESCRIPTION.value,
                PermissionAction.MANAGE_TAGS.value,
                PermissionAction.EXPORT_REPORTS.value,
            ])
        elif "ANALYST" in norm or "VIEWER" in norm:
            perms.update([
                PermissionAction.VIEW_METADATA.value,
                PermissionAction.EXPORT_REPORTS.value,
            ])
        elif "ADMIN" in norm:
            perms.update([a.value for a in PermissionAction])

    # Evaluate dynamic database rules
    if role_ids:
        stmt = select(PermissionRule).where(PermissionRule.role_id.in_(role_ids))
        res = await session.execute(stmt)
        rules = res.scalars().all()
        for rule in rules:
            if rule.effect == PermissionEffect.ALLOW:
                perms.add(rule.action.value)
            elif rule.effect == PermissionEffect.DENY and rule.resource_type == ResourceType.GLOBAL:
                perms.discard(rule.action.value)

    if not perms:
        perms.add(PermissionAction.VIEW_METADATA.value)

    return sorted(list(perms))


@router.post(
    "/login",
    response_model=AuthResponse,
    summary="Authenticate user and receive JWT access token",
)
async def login(
    payload: LoginRequest,
    db: AsyncSession = Depends(get_db_session),
) -> AuthResponse:
    """
    Authenticates user credentials against Argon2id hashes, validates account active status,
    and returns a signed JWT access token with effective permissions.
    """
    identifier = payload.username_or_email or payload.username or payload.email
    if not identifier:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A username, username_or_email, or email must be provided.",
        )

    try:
        user = await auth_service.authenticate_user(
            session=db,
            username_or_email=identifier,
            password=payload.password,
        )
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except InactiveUserError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc

    roles = await auth_service.get_user_roles(db, user.id)

    # Get role IDs
    role_ids_stmt = select(UserRole.role_id).where(UserRole.user_id == user.id)
    role_ids_res = await db.execute(role_ids_stmt)
    role_ids = list(role_ids_res.scalars().all())

    permissions = await resolve_effective_permissions(db, user, role_ids, roles)

    token = auth_service.create_access_token(
        data={
            "sub": str(user.id),
            "username": user.username,
            "email": user.email,
            "roles": roles,
            "is_superadmin": user.is_superadmin,
            "permissions": permissions,
        }
    )

    user_ctx = AuthUserContext(
        id=str(user.id),
        username=user.username,
        email=user.email,
        roles=roles,
        is_superadmin=user.is_superadmin,
        permissions=permissions,
    )

    return AuthResponse(
        access_token=token,
        token_type="bearer",
        user=user_ctx,
    )


@router.get(
    "/me",
    response_model=AuthUserContext,
    summary="Get current authenticated user claims",
)
async def get_current_user_profile(
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> AuthUserContext:
    """
    Returns the authenticated user context extracted from JWT claims with permissions.
    """
    roles = await auth_service.get_user_roles(db, user_ctx.user_id)
    permissions = await resolve_effective_permissions(
        db, user_ctx.user, user_ctx.role_ids, roles
    )

    return AuthUserContext(
        id=str(user_ctx.user_id),
        username=user_ctx.user.username,
        email=user_ctx.user.email,
        roles=roles,
        is_superadmin=user_ctx.is_superadmin,
        permissions=permissions,
    )
