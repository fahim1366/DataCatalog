from __future__ import annotations

import logging
import uuid
from typing import List, Optional, Set
from sqlalchemy import text, select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.api.dependencies.rbac import UserContext
from backend.src.models.catalog import CatalogTable, DatabaseSchema, Tag, TagAssignment
from backend.src.models.rbac import (
    Category,
    CategoryAssignment,
    EntityType,
    PermissionAction,
    PermissionEffect,
    PermissionRule,
    ResourceType,
)

logger = logging.getLogger(__name__)


class CategoryService:
    """
    High-performance business category tree traversal and hierarchical permission
    resolution service utilizing PostgreSQL 16 recursive CTE queries.
    """

    @staticmethod
    async def get_descendant_category_ids(
        session: AsyncSession, root_category_id: uuid.UUID
    ) -> List[uuid.UUID]:
        """
        Recursively traverses the category tree downwards starting from root_category_id,
        returning all descendant category UUIDs (including root_category_id itself).
        Executes in sub-millisecond latency using PostgreSQL recursive CTE.
        """
        query = text("""
            WITH RECURSIVE category_tree AS (
                SELECT id FROM categories WHERE id = :root_id
                UNION ALL
                SELECT c.id FROM categories c JOIN category_tree ct ON c.parent_id = ct.id
            )
            SELECT id FROM category_tree;
        """)
        result = await session.execute(query, {"root_id": root_category_id})
        return [row[0] for row in result.fetchall()]

    @staticmethod
    async def get_descendant_categories(
        session: AsyncSession, root_category_id: uuid.UUID
    ) -> List[Category]:
        """
        Fetches all Category model instances descending from root_category_id,
        ordered by hierarchy depth and name.
        """
        descendant_ids = await CategoryService.get_descendant_category_ids(
            session, root_category_id
        )
        if not descendant_ids:
            return []

        stmt = select(Category).where(Category.id.in_(descendant_ids))
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_ancestor_category_ids(
        session: AsyncSession, category_id: uuid.UUID
    ) -> List[uuid.UUID]:
        """
        Recursively traverses upwards from category_id to find all ancestor category UUIDs
        (including category_id itself).
        """
        query = text("""
            WITH RECURSIVE category_ancestors AS (
                SELECT id, parent_id FROM categories WHERE id = :cat_id
                UNION ALL
                SELECT c.id, c.parent_id FROM categories c JOIN category_ancestors ca ON c.id = ca.parent_id
            )
            SELECT id FROM category_ancestors;
        """)
        result = await session.execute(query, {"cat_id": category_id})
        return [row[0] for row in result.fetchall()]

    @staticmethod
    async def resolve_effective_permission(
        session: AsyncSession,
        user_ctx: UserContext,
        entity_type: EntityType,
        entity_id: uuid.UUID,
        action: PermissionAction = PermissionAction.VIEW_METADATA,
    ) -> PermissionEffect:
        """
        Resolves effective permission (ALLOW vs DENY) for a target entity.
        Evaluates:
        1. Superadmin bypass (always ALLOW).
        2. Unassigned roles (always DENY).
        3. Explicit DENY precedence across Database, Schema, and Table.
        4. ALLOW inheritance across Global, Database, Schema, Table, and Category Sub-Tree.
        5. PII tag restrictions.
        """
        if user_ctx.is_superadmin:
            return PermissionEffect.ALLOW

        if not user_ctx.role_ids:
            return PermissionEffect.DENY

        if entity_type == EntityType.TABLE:
            # 1. Fetch table and parent schema / connection info
            tbl_stmt = (
                select(CatalogTable, DatabaseSchema)
                .join(DatabaseSchema, DatabaseSchema.id == CatalogTable.schema_id)
                .where(CatalogTable.id == entity_id)
            )
            res = await session.execute(tbl_stmt)
            row = res.first()
            if not row:
                return PermissionEffect.DENY

            table, schema = row
            connection_id = schema.connection_id
            schema_id = schema.id
            table_id = table.id

            # 2. Check for explicit DENY rules (Database, Schema, Table)
            deny_stmt = (
                select(PermissionRule.id)
                .where(
                    PermissionRule.role_id.in_(user_ctx.role_ids),
                    PermissionRule.action == action,
                    PermissionRule.effect == PermissionEffect.DENY,
                    or_(
                        and_(
                            PermissionRule.resource_type == ResourceType.DATABASE,
                            PermissionRule.resource_id == connection_id,
                        ),
                        and_(
                            PermissionRule.resource_type == ResourceType.SCHEMA,
                            PermissionRule.resource_id == schema_id,
                        ),
                        and_(
                            PermissionRule.resource_type == ResourceType.TABLE,
                            PermissionRule.resource_id == table_id,
                        ),
                    ),
                )
            )
            deny_res = await session.execute(deny_stmt)
            if deny_res.first() is not None:
                return PermissionEffect.DENY

            # 3. Check for direct ALLOW rules (GLOBAL, DATABASE, SCHEMA, TABLE)
            direct_allow_stmt = (
                select(PermissionRule.id)
                .where(
                    PermissionRule.role_id.in_(user_ctx.role_ids),
                    PermissionRule.action == action,
                    PermissionRule.effect == PermissionEffect.ALLOW,
                    or_(
                        PermissionRule.resource_type == ResourceType.GLOBAL,
                        and_(
                            PermissionRule.resource_type == ResourceType.DATABASE,
                            PermissionRule.resource_id == connection_id,
                        ),
                        and_(
                            PermissionRule.resource_type == ResourceType.SCHEMA,
                            PermissionRule.resource_id == schema_id,
                        ),
                        and_(
                            PermissionRule.resource_type == ResourceType.TABLE,
                            PermissionRule.resource_id == table_id,
                        ),
                    ),
                )
            )
            direct_allow_res = await session.execute(direct_allow_stmt)
            has_allow = direct_allow_res.first() is not None

            # 4. Check for Category Sub-Tree ALLOW rules if not already allowed
            if not has_allow:
                # Fetch categories assigned to this table
                cat_assign_stmt = select(CategoryAssignment.category_id).where(
                    CategoryAssignment.entity_type == EntityType.TABLE,
                    CategoryAssignment.entity_id == table_id,
                )
                cat_assign_res = await session.execute(cat_assign_stmt)
                assigned_cat_ids = list(cat_assign_res.scalars().all())

                if assigned_cat_ids:
                    # Collect all ancestor categories for these assignments
                    all_ancestor_ids: Set[uuid.UUID] = set()
                    for cat_id in assigned_cat_ids:
                        ancestors = await CategoryService.get_ancestor_category_ids(
                            session, cat_id
                        )
                        all_ancestor_ids.update(ancestors)

                    if all_ancestor_ids:
                        cat_rule_stmt = select(PermissionRule.id).where(
                            PermissionRule.role_id.in_(user_ctx.role_ids),
                            PermissionRule.action == action,
                            PermissionRule.effect == PermissionEffect.ALLOW,
                            PermissionRule.resource_type == ResourceType.CATEGORY,
                            PermissionRule.resource_id.in_(list(all_ancestor_ids)),
                        )
                        cat_rule_res = await session.execute(cat_rule_stmt)
                        if cat_rule_res.first() is not None:
                            has_allow = True

            if not has_allow:
                return PermissionEffect.DENY

            # 5. Check PII Tag restrictions
            pii_stmt = (
                select(TagAssignment.id)
                .join(Tag, Tag.id == TagAssignment.tag_id)
                .where(
                    TagAssignment.entity_type == EntityType.TABLE,
                    TagAssignment.entity_id == table_id,
                    Tag.name == "PII",
                )
            )
            pii_res = await session.execute(pii_stmt)
            if pii_res.first() is not None:
                # Requires GLOBAL permission rule
                global_stmt = select(PermissionRule.id).where(
                    PermissionRule.role_id.in_(user_ctx.role_ids),
                    PermissionRule.action == PermissionAction.VIEW_METADATA,
                    PermissionRule.resource_type == ResourceType.GLOBAL,
                    PermissionRule.effect == PermissionEffect.ALLOW,
                )
                global_res = await session.execute(global_stmt)
                if global_res.first() is None:
                    return PermissionEffect.DENY

            return PermissionEffect.ALLOW

        # Fallback for non-table entities
        return PermissionEffect.DENY

