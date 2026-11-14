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
from backend.src.models.identity import Group, GroupRole, User, UserGroup, UserRole, UserStatus
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
    """Holds active user session state, resolved role assignments, group memberships, and superadmin status."""

    def __init__(
        self,
        user: User,
        role_ids: List[uuid.UUID],
        is_superadmin: bool,
        group_ids: Optional[List[uuid.UUID]] = None,
        direct_role_ids: Optional[List[uuid.UUID]] = None,
    ) -> None:
        self.user = user
        self.role_ids = role_ids
        self.is_superadmin = is_superadmin
        self.group_ids = group_ids or []
        self.direct_role_ids = direct_role_ids or []

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.id


def user_rule_matches(user_ctx: UserContext):
    """Constructs SQL OR clause matching rules for the user directly, through groups, or through roles."""
    conditions = [PermissionRule.user_id == user_ctx.user_id]
    if user_ctx.group_ids:
        conditions.append(PermissionRule.group_id.in_(user_ctx.group_ids))
    if user_ctx.role_ids:
        conditions.append(PermissionRule.role_id.in_(user_ctx.role_ids))
    return or_(*conditions)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(security),
    db: AsyncSession = Depends(get_db_session),
) -> UserContext:
    """
    FastAPI security dependency validating JWT Bearer tokens and returning UserContext.
    Enforces that user accounts are ACTIVE and resolves role and group hierarchy.
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

    # Fetch user's direct assigned role IDs
    roles_stmt = select(UserRole.role_id).where(UserRole.user_id == user.id)
    result = await db.execute(roles_stmt)
    direct_role_ids = list(result.scalars().all())

    # Fetch user's active group IDs
    groups_stmt = (
        select(UserGroup.group_id)
        .join(Group, Group.id == UserGroup.group_id)
        .where(UserGroup.user_id == user.id, Group.is_active == True)
    )
    group_res = await db.execute(groups_stmt)
    group_ids = list(group_res.scalars().all())

    # Fetch roles assigned to user's active groups
    group_role_ids: List[uuid.UUID] = []
    if group_ids:
        group_roles_stmt = select(GroupRole.role_id).where(GroupRole.group_id.in_(group_ids))
        group_roles_res = await db.execute(group_roles_stmt)
        group_role_ids = list(group_roles_res.scalars().all())

    all_role_ids = list(set(direct_role_ids + group_role_ids))

    return UserContext(
        user=user,
        role_ids=all_role_ids,
        is_superadmin=user.is_superadmin,
        group_ids=group_ids,
        direct_role_ids=direct_role_ids,
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
            user_rule_matches(user_ctx),
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
                        or_(
                            PermissionRule.resource_id == CategoryAssignment.category_id,
                            text("""
                                EXISTS (
                                    WITH RECURSIVE cat_tree AS (
                                        SELECT id FROM categories WHERE id = permission_rules.resource_id
                                        UNION ALL
                                        SELECT c.id FROM categories c JOIN cat_tree ct ON c.parent_id = ct.id
                                    )
                                    SELECT 1 FROM cat_tree WHERE cat_tree.id = category_assignments.category_id
                                )
                            """),
                        ),
                    )
                    .correlate_except(CategoryAssignment)
                ),
            ),
        )
        .correlate_except(PermissionRule, DatabaseSchema)
    )

    # 2. DENY Subquery: Explicit table, schema, database, or category DENY overrides ALLOW
    deny_subquery = exists(
        select(1)
        .select_from(PermissionRule)
        .join(DatabaseSchema, DatabaseSchema.id == CatalogTable.schema_id)
        .where(
            user_rule_matches(user_ctx),
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
                exists(
                    select(1)
                    .select_from(CategoryAssignment)
                    .where(
                        CategoryAssignment.entity_type == EntityType.TABLE,
                        CategoryAssignment.entity_id == CatalogTable.id,
                        PermissionRule.resource_type == ResourceType.CATEGORY,
                        or_(
                            PermissionRule.resource_id == CategoryAssignment.category_id,
                            text("""
                                EXISTS (
                                    WITH RECURSIVE cat_tree AS (
                                        SELECT id FROM categories WHERE id = permission_rules.resource_id
                                        UNION ALL
                                        SELECT c.id FROM categories c JOIN cat_tree ct ON c.parent_id = ct.id
                                    )
                                    SELECT 1 FROM cat_tree WHERE cat_tree.id = category_assignments.category_id
                                )
                            """),
                        ),
                    )
                    .correlate_except(CategoryAssignment)
                ),
            ),
        )
        .correlate_except(PermissionRule, DatabaseSchema)
    )

    # 3. PII Tag Restriction: Tables with PII tag require GLOBAL permission, EDIT_DESCRIPTION, MANAGE_TAGS, or explicit table/category ALLOW
    pii_restriction = exists(
        select(1)
        .select_from(TagAssignment)
        .join(Tag, Tag.id == TagAssignment.tag_id)
        .where(
            TagAssignment.entity_type == EntityType.TABLE,
            TagAssignment.entity_id == CatalogTable.id,
            Tag.name == "PII",
            not_(
                or_(
                    # 1. Global VIEW_METADATA
                    exists(
                        select(1)
                        .select_from(PermissionRule)
                        .where(
                            user_rule_matches(user_ctx),
                            PermissionRule.action == PermissionAction.VIEW_METADATA,
                            PermissionRule.resource_type == ResourceType.GLOBAL,
                            PermissionRule.effect == PermissionEffect.ALLOW,
                        )
                    ),
                    # 2. EDIT_DESCRIPTION or MANAGE_TAGS (e.g. Data Steward)
                    exists(
                        select(1)
                        .select_from(PermissionRule)
                        .where(
                            user_rule_matches(user_ctx),
                            PermissionRule.action.in_([
                                PermissionAction.EDIT_DESCRIPTION,
                                PermissionAction.MANAGE_TAGS,
                            ]),
                            PermissionRule.effect == PermissionEffect.ALLOW,
                        )
                    ),
                    # 3. Explicit table ALLOW
                    exists(
                        select(1)
                        .select_from(PermissionRule)
                        .where(
                            user_rule_matches(user_ctx),
                            PermissionRule.resource_type == ResourceType.TABLE,
                            PermissionRule.resource_id == CatalogTable.id,
                            PermissionRule.action == action,
                            PermissionRule.effect == PermissionEffect.ALLOW,
                        )
                    ),
                    # 4. Explicit category ALLOW (including hierarchical subcategories)
                    exists(
                        select(1)
                        .select_from(CategoryAssignment)
                        .join(PermissionRule, PermissionRule.resource_id == CategoryAssignment.category_id)
                        .where(
                            CategoryAssignment.entity_type == EntityType.TABLE,
                            CategoryAssignment.entity_id == CatalogTable.id,
                            user_rule_matches(user_ctx),
                            PermissionRule.resource_type == ResourceType.CATEGORY,
                            PermissionRule.action == action,
                            PermissionRule.effect == PermissionEffect.ALLOW,
                            or_(
                                PermissionRule.resource_id == CategoryAssignment.category_id,
                                text("""
                                    EXISTS (
                                        WITH RECURSIVE cat_tree AS (
                                            SELECT id FROM categories WHERE id = permission_rules.resource_id
                                            UNION ALL
                                            SELECT c.id FROM categories c JOIN cat_tree ct ON c.parent_id = ct.id
                                        )
                                        SELECT 1 FROM cat_tree WHERE cat_tree.id = category_assignments.category_id
                                    )
                                """),
                            ),
                        )
                        .correlate_except(CategoryAssignment, PermissionRule)
                    ),
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


def require_permission(action: PermissionAction) -> Callable:
    """
    FastAPI dependency factory enforcing that the authenticated user possesses
    a specific PermissionAction (via superadmin bypass or explicit ALLOW rule).
    Enforces that explicit DENY rules take absolute precedence.
    """
    async def permission_dependency(
        user_ctx: UserContext = Depends(get_current_user),
        db: AsyncSession = Depends(get_db_session),
    ) -> UserContext:
        if user_ctx.is_superadmin:
            return user_ctx

        # 1. Check explicit DENY
        deny_stmt = select(1).where(
            user_rule_matches(user_ctx),
            PermissionRule.action == action,
            PermissionRule.effect == PermissionEffect.DENY,
        )
        deny_res = await db.execute(deny_stmt)
        if deny_res.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Forbidden: Access for {action.value} is explicitly denied.",
            )

        # 2. Check explicit ALLOW
        allow_stmt = select(1).where(
            user_rule_matches(user_ctx),
            PermissionRule.action == action,
            PermissionRule.effect == PermissionEffect.ALLOW,
        )
        allow_res = await db.execute(allow_stmt)
        if allow_res.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Forbidden: User lacks {action.value} permission.",
            )

        return user_ctx

    return permission_dependency

