from __future__ import annotations

from typing import Callable, List, Optional
import uuid
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import Select, and_, exists, not_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.database import get_db_session
from backend.src.models.catalog import (
    CatalogTable,
    DatabaseSchema,
    Tag,
    TagAssignment,
)
from backend.src.models.identity import User, UserRole, UserStatus
from backend.src.models.rbac import (
    CategoryAssignment,
    EntityType,
    PermissionAction,
    PermissionEffect,
    PermissionRule,
    ResourceType,
)
from backend.src.services.auth.jwt_service import (
    AuthError,
    auth_service,
)

security = HTTPBearer(auto_error=True)


class UserContext:
    """Holds active user session state, resolved role assignments, and superadmin status."""

    def __init__(
        self,
        user: User,
        role_ids: List[uuid.UUID],
        is_superadmin: bool,
    ) -> None:
        self.user = user
        self.role_ids = role_ids
        self.is_superadmin = is_superadmin

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.id


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(security),
    db: AsyncSession = Depends(get_db_session),
) -> UserContext:
    """
    FastAPI security dependency validating JWT Bearer tokens and returning UserContext.
    Enforces that user accounts are ACTIVE.
    """
    token = credentials.credentials
    try:
        user_id = auth_service.extract_user_id(token)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user = await db.get(User, user_id)
    if not user or user.status != UserStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Fetch user's assigned role IDs
    roles_stmt = select(UserRole.role_id).where(UserRole.user_id == user.id)
    result = await db.execute(roles_stmt)
    role_ids = list(result.scalars().all())

    return UserContext(
        user=user,
        role_ids=role_ids,
        is_superadmin=user.is_superadmin,
    )


def build_table_rbac_filter(
    user_ctx: UserContext,
    action: PermissionAction = PermissionAction.VIEW_METADATA,
) -> Callable[[Select], Select]:
    """
    Compiles SQL EXISTS subqueries directly enforcing ALLOW/DENY hierarchy and tag policies.
    Returns a query transformer Callable[[Select], Select].
    """
    if user_ctx.is_superadmin:
        # Superadmin bypasses RBAC filtering completely
        return lambda query: query

    if not user_ctx.role_ids:
        # User has no roles -> deny all access
        return lambda query: query.where(text("1 = 0"))

    # 1. ALLOW Subquery: Inherited from GLOBAL, DATABASE, SCHEMA, direct TABLE, or CATEGORY
    allow_subquery = exists(
        select(1)
        .select_from(PermissionRule)
        .join(DatabaseSchema, DatabaseSchema.id == CatalogTable.schema_id)
        .where(
            PermissionRule.role_id.in_(user_ctx.role_ids),
            PermissionRule.action == action,
            PermissionRule.effect == PermissionEffect.ALLOW,
            or_(
                PermissionRule.resource_type == ResourceType.GLOBAL,
                and_(
                    PermissionRule.resource_type == ResourceType.DATABASE,
                    PermissionRule.resource_id == DatabaseSchema.connection_id,
                ),
                and_(
                    PermissionRule.resource_type == ResourceType.SCHEMA,
                    PermissionRule.resource_id == CatalogTable.schema_id,
                ),
                and_(
                    PermissionRule.resource_type == ResourceType.TABLE,
                    PermissionRule.resource_id == CatalogTable.id,
                ),
                exists(
                    select(1)
                    .select_from(CategoryAssignment)
                    .where(
                        CategoryAssignment.entity_type == EntityType.TABLE,
                        CategoryAssignment.entity_id == CatalogTable.id,
                        PermissionRule.resource_type == ResourceType.CATEGORY,
                        PermissionRule.resource_id == CategoryAssignment.category_id,
                    )
                ),
            ),
        )
    )

    # 2. DENY Subquery: Explicit table, schema, or database DENY overrides ALLOW
    deny_subquery = exists(
        select(1)
        .select_from(PermissionRule)
        .join(DatabaseSchema, DatabaseSchema.id == CatalogTable.schema_id)
        .where(
            PermissionRule.role_id.in_(user_ctx.role_ids),
            PermissionRule.action == action,
            PermissionRule.effect == PermissionEffect.DENY,
            or_(
                and_(
                    PermissionRule.resource_type == ResourceType.DATABASE,
                    PermissionRule.resource_id == DatabaseSchema.connection_id,
                ),
                and_(
                    PermissionRule.resource_type == ResourceType.SCHEMA,
                    PermissionRule.resource_id == CatalogTable.schema_id,
                ),
                and_(
                    PermissionRule.resource_type == ResourceType.TABLE,
                    PermissionRule.resource_id == CatalogTable.id,
                ),
            ),
        )
    )

    # 3. PII Tag Restriction: Tables with PII tag require GLOBAL permission (or explicit bypass)
    pii_restriction = exists(
        select(1)
        .select_from(TagAssignment)
        .join(Tag, Tag.id == TagAssignment.tag_id)
        .where(
            TagAssignment.entity_type == EntityType.TABLE,
            TagAssignment.entity_id == CatalogTable.id,
            Tag.name == "PII",
            not_(
                exists(
                    select(1)
                    .select_from(PermissionRule)
                    .where(
                        PermissionRule.role_id.in_(user_ctx.role_ids),
                        PermissionRule.action == PermissionAction.VIEW_METADATA,
                        PermissionRule.resource_type == ResourceType.GLOBAL,
                    )
                )
            ),
        )
    )

    def apply_rbac_filter(query: Select) -> Select:
        return query.where(
            and_(
                allow_subquery,
                not_(deny_subquery),
                not_(pii_restriction),
            )
        )

    return apply_rbac_filter


def require_table_rbac(action: PermissionAction = PermissionAction.VIEW_METADATA) -> Callable:
    """
    FastAPI dependency factory yielding a SQL query modifier enforcing table RBAC.
    """
    async def rbac_filter_dependency(
        user_ctx: UserContext = Depends(get_current_user),
    ) -> Callable[[Select], Select]:
        return build_table_rbac_filter(user_ctx=user_ctx, action=action)

    return rbac_filter_dependency

