from __future__ import annotations

import logging
import uuid
from typing import Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, and_, or_, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.api.dependencies.rbac import UserContext
from backend.src.models.catalog import CatalogTable, DatabaseSchema, LifecycleStatus
from backend.src.models.connections import DatabaseConnection
from backend.src.models.identity import Group, GroupRole, Role, User, UserGroup, UserRole
from backend.src.models.rbac import (
    CategoryAssignment,
    EntityType,
    PermissionAction,
    PermissionEffect,
    PermissionRule,
    ResourceType,
)
from backend.src.services.rbac.category_service import CategoryService

logger = logging.getLogger(__name__)


class EffectivePermissionRow(BaseModel):
    id: str  # unique identifier for frontend tracking
    database_id: uuid.UUID
    database_name: str
    schema_id: Optional[uuid.UUID] = None
    schema_name: Optional[str] = None
    table_id: Optional[uuid.UUID] = None
    table_name: Optional[str] = None
    resource_type: ResourceType
    resource_name: str
    permission: PermissionAction
    result: PermissionEffect
    source_type: str  # 'SUPERADMIN' | 'DIRECT_USER' | 'ROLE' | 'GROUP' | 'PARENT_RESOURCE' | 'CATEGORY' | 'DEFAULT_DENY'
    source_name: str
    is_inherited: bool
    rule_id: Optional[uuid.UUID] = None
    explanation: str

    model_config = ConfigDict(from_attributes=True)


class EffectivePermissionService:
    """
    Computes explainable effective permissions for a user across the physical
    hierarchy (Database -> Schema -> Table) and category taxonomy.
    """

    @staticmethod
    async def get_user_effective_permissions(
        session: AsyncSession,
        user_id: uuid.UUID,
        resource_query: Optional[str] = None,
        permission_action: Optional[PermissionAction] = None,
    ) -> List[EffectivePermissionRow]:
        user = await session.get(User, user_id)
        if not user:
            return []

        # 1. Fetch user roles & groups
        user_roles_stmt = select(UserRole.role_id, Role.name).join(Role, Role.id == UserRole.role_id).where(UserRole.user_id == user.id)
        u_roles_res = await session.execute(user_roles_stmt)
        role_map: Dict[uuid.UUID, str] = {r[0]: r[1] for r in u_roles_res.all()}

        user_groups_stmt = (
            select(UserGroup.group_id, Group.name)
            .join(Group, Group.id == UserGroup.group_id)
            .where(UserGroup.user_id == user.id, Group.is_active == True)
        )
        u_groups_res = await session.execute(user_groups_stmt)
        group_map: Dict[uuid.UUID, str] = {g[0]: g[1] for g in u_groups_res.all()}

        group_roles_stmt = (
            select(GroupRole.role_id, Role.name)
            .join(Role, Role.id == GroupRole.role_id)
            .where(GroupRole.group_id.in_(list(group_map.keys())))
        ) if group_map else None
        if group_roles_stmt is not None:
            g_roles_res = await session.execute(group_roles_stmt)
            for rid, rname in g_roles_res.all():
                role_map[rid] = rname

        user_role_ids = list(role_map.keys())
        user_group_ids = list(group_map.keys())

        # 2. Fetch all permission rules applying to this user
        rules_conditions = [PermissionRule.user_id == user.id]
        if user_group_ids:
            rules_conditions.append(PermissionRule.group_id.in_(user_group_ids))
        if user_role_ids:
            rules_conditions.append(PermissionRule.role_id.in_(user_role_ids))

        rules_stmt = select(PermissionRule).where(or_(*rules_conditions))
        rules_res = await session.execute(rules_stmt)
        all_rules = list(rules_res.scalars().all())

        # 3. Fetch physical resources: Databases, Schemas, Tables
        tbl_stmt = (
            select(
                DatabaseConnection.id.label("db_id"),
                DatabaseConnection.name.label("db_name"),
                DatabaseSchema.id.label("schema_id"),
                DatabaseSchema.name.label("schema_name"),
                CatalogTable.id.label("table_id"),
                CatalogTable.name.label("table_name"),
            )
            .join(DatabaseSchema, DatabaseSchema.connection_id == DatabaseConnection.id)
            .join(CatalogTable, CatalogTable.schema_id == DatabaseSchema.id)
            .where(CatalogTable.status != LifecycleStatus.DELETED)
            .order_by(DatabaseConnection.name.asc(), DatabaseSchema.name.asc(), CatalogTable.name.asc())
        )
        if resource_query:
            q_like = f"%{resource_query.strip()}%"
            tbl_stmt = tbl_stmt.where(
                or_(
                    DatabaseConnection.name.ilike(q_like),
                    DatabaseSchema.name.ilike(q_like),
                    CatalogTable.name.ilike(q_like),
                )
            )

        tbl_rows = (await session.execute(tbl_stmt)).all()

        actions_to_check = [permission_action] if permission_action else [
            PermissionAction.VIEW_METADATA,
            PermissionAction.EDIT_DESCRIPTION,
            PermissionAction.MANAGE_TAGS,
            PermissionAction.MANAGE_CONNECTIONS,
        ]

        results: List[EffectivePermissionRow] = []

        # If user is superadmin: all permissions are ALLOW
        if user.is_superadmin:
            for row in tbl_rows:
                for act in actions_to_check:
                    results.append(
                        EffectivePermissionRow(
                            id=f"{row.table_id}_{act.value}",
                            database_id=row.db_id,
                            database_name=row.db_name,
                            schema_id=row.schema_id,
                            schema_name=row.schema_name,
                            table_id=row.table_id,
                            table_name=row.table_name,
                            resource_type=ResourceType.TABLE,
                            resource_name=row.table_name,
                            permission=act,
                            result=PermissionEffect.ALLOW,
                            source_type="SUPERADMIN",
                            source_name="مدیر ارشد سامانه (SuperAdmin)",
                            is_inherited=False,
                            rule_id=None,
                            explanation="کاربر دارای امتیاز دسترسی تام (SuperAdmin) به کلیه منابع سامانه است.",
                        )
                    )
            return results

        # Index user rules by (resource_type, resource_id, action)
        rules_by_target: Dict[Tuple[ResourceType, Optional[uuid.UUID], PermissionAction], List[PermissionRule]] = {}
        for r in all_rules:
            key = (r.resource_type, r.resource_id, r.action)
            if key not in rules_by_target:
                rules_by_target[key] = []
            rules_by_target[key].append(r)

        # Helper to describe rule source
        def describe_source(r: PermissionRule) -> Tuple[str, str]:
            if r.user_id == user.id:
                return "DIRECT_USER", "تخصیص صریح به کاربر (Direct User Assignment)"
            if r.group_id and r.group_id in group_map:
                return "GROUP", f"عضویت در گروه «{group_map[r.group_id]}»"
            if r.role_id and r.role_id in role_map:
                return "ROLE", f"نقش سازمانی «{role_map[r.role_id]}»"
            return "ROLE", "قانون سازمانی"

        for row in tbl_rows:
            db_id = row.db_id
            sch_id = row.schema_id
            tbl_id = row.table_id

            for act in actions_to_check:
                # 1. Check Table DENY
                tbl_denies = [r for r in rules_by_target.get((ResourceType.TABLE, tbl_id, act), []) if r.effect == PermissionEffect.DENY]
                if tbl_denies:
                    stype, sname = describe_source(tbl_denies[0])
                    results.append(
                        EffectivePermissionRow(
                            id=f"{tbl_id}_{act.value}",
                            database_id=db_id,
                            database_name=row.db_name,
                            schema_id=sch_id,
                            schema_name=row.schema_name,
                            table_id=tbl_id,
                            table_name=row.table_name,
                            resource_type=ResourceType.TABLE,
                            resource_name=row.table_name,
                            permission=act,
                            result=PermissionEffect.DENY,
                            source_type=stype,
                            source_name=sname,
                            is_inherited=False,
                            rule_id=tbl_denies[0].id,
                            explanation=f"قانون مسدودسازی صریح (DENY) روی جدول دارای بالاترین اولویت است ({sname}).",
                        )
                    )
                    continue

                # 2. Check Schema DENY
                sch_denies = [r for r in rules_by_target.get((ResourceType.SCHEMA, sch_id, act), []) if r.effect == PermissionEffect.DENY]
                if sch_denies:
                    stype, sname = describe_source(sch_denies[0])
                    results.append(
                        EffectivePermissionRow(
                            id=f"{tbl_id}_{act.value}",
                            database_id=db_id,
                            database_name=row.db_name,
                            schema_id=sch_id,
                            schema_name=row.schema_name,
                            table_id=tbl_id,
                            table_name=row.table_name,
                            resource_type=ResourceType.TABLE,
                            resource_name=row.table_name,
                            permission=act,
                            result=PermissionEffect.DENY,
                            source_type=stype,
                            source_name=sname,
                            is_inherited=True,
                            rule_id=sch_denies[0].id,
                            explanation=f"قانون منع موروثی (DENY) از اسکیما «{row.schema_name}» به جدول اعمال شده است.",
                        )
                    )
                    continue

                # 3. Check Database DENY
                db_denies = [r for r in rules_by_target.get((ResourceType.DATABASE, db_id, act), []) if r.effect == PermissionEffect.DENY]
                if db_denies:
                    stype, sname = describe_source(db_denies[0])
                    results.append(
                        EffectivePermissionRow(
                            id=f"{tbl_id}_{act.value}",
                            database_id=db_id,
                            database_name=row.db_name,
                            schema_id=sch_id,
                            schema_name=row.schema_name,
                            table_id=tbl_id,
                            table_name=row.table_name,
                            resource_type=ResourceType.TABLE,
                            resource_name=row.table_name,
                            permission=act,
                            result=PermissionEffect.DENY,
                            source_type=stype,
                            source_name=sname,
                            is_inherited=True,
                            rule_id=db_denies[0].id,
                            explanation=f"قانون منع موروثی (DENY) از پایگاه‌داده «{row.db_name}» به تمام زیرمجموعه‌ها اعمال شده است.",
                        )
                    )
                    continue

                # 4. Check Table Direct ALLOW
                tbl_allows = [r for r in rules_by_target.get((ResourceType.TABLE, tbl_id, act), []) if r.effect == PermissionEffect.ALLOW]
                if tbl_allows:
                    stype, sname = describe_source(tbl_allows[0])
                    results.append(
                        EffectivePermissionRow(
                            id=f"{tbl_id}_{act.value}",
                            database_id=db_id,
                            database_name=row.db_name,
                            schema_id=sch_id,
                            schema_name=row.schema_name,
                            table_id=tbl_id,
                            table_name=row.table_name,
                            resource_type=ResourceType.TABLE,
                            resource_name=row.table_name,
                            permission=act,
                            result=PermissionEffect.ALLOW,
                            source_type=stype,
                            source_name=sname,
                            is_inherited=False,
                            rule_id=tbl_allows[0].id,
                            explanation=f"مجوز صریح (ALLOW) به طور مستقیم روی این جدول اعمال شده است ({sname}).",
                        )
                    )
                    continue

                # 5. Check Schema Inherited ALLOW
                sch_allows = [r for r in rules_by_target.get((ResourceType.SCHEMA, sch_id, act), []) if r.effect == PermissionEffect.ALLOW]
                if sch_allows:
                    stype, sname = describe_source(sch_allows[0])
                    results.append(
                        EffectivePermissionRow(
                            id=f"{tbl_id}_{act.value}",
                            database_id=db_id,
                            database_name=row.db_name,
                            schema_id=sch_id,
                            schema_name=row.schema_name,
                            table_id=tbl_id,
                            table_name=row.table_name,
                            resource_type=ResourceType.TABLE,
                            resource_name=row.table_name,
                            permission=act,
                            result=PermissionEffect.ALLOW,
                            source_type="PARENT_RESOURCE",
                            source_name=f"اسکیما «{row.schema_name}» ({sname})",
                            is_inherited=True,
                            rule_id=sch_allows[0].id,
                            explanation=f"مجوز به صورت آبشاری از سطح اسکیمای والد «{row.schema_name}» ارث‌بری شده است.",
                        )
                    )
                    continue

                # 6. Check Database Inherited ALLOW
                db_allows = [r for r in rules_by_target.get((ResourceType.DATABASE, db_id, act), []) if r.effect == PermissionEffect.ALLOW]
                if db_allows:
                    stype, sname = describe_source(db_allows[0])
                    results.append(
                        EffectivePermissionRow(
                            id=f"{tbl_id}_{act.value}",
                            database_id=db_id,
                            database_name=row.db_name,
                            schema_id=sch_id,
                            schema_name=row.schema_name,
                            table_id=tbl_id,
                            table_name=row.table_name,
                            resource_type=ResourceType.TABLE,
                            resource_name=row.table_name,
                            permission=act,
                            result=PermissionEffect.ALLOW,
                            source_type="PARENT_RESOURCE",
                            source_name=f"پایگاه داده «{row.db_name}» ({sname})",
                            is_inherited=True,
                            rule_id=db_allows[0].id,
                            explanation=f"مجوز به صورت سلسله‌مراتبی از کل پایگاه داده «{row.db_name}» به جدول ارث‌بری شده است.",
                        )
                    )
                    continue

                # 7. Check Global ALLOW
                global_allows = [r for r in rules_by_target.get((ResourceType.GLOBAL, None, act), []) if r.effect == PermissionEffect.ALLOW]
                if global_allows:
                    stype, sname = describe_source(global_allows[0])
                    results.append(
                        EffectivePermissionRow(
                            id=f"{tbl_id}_{act.value}",
                            database_id=db_id,
                            database_name=row.db_name,
                            schema_id=sch_id,
                            schema_name=row.schema_name,
                            table_id=tbl_id,
                            table_name=row.table_name,
                            resource_type=ResourceType.TABLE,
                            resource_name=row.table_name,
                            permission=act,
                            result=PermissionEffect.ALLOW,
                            source_type="GLOBAL",
                            source_name=f"دسترسی سراسری GLOBAL ({sname})",
                            is_inherited=True,
                            rule_id=global_allows[0].id,
                            explanation=f"مجوز از طریق قانون دسترسی سراسری سازمان به صورت خودکار برقرار است ({sname}).",
                        )
                    )
                    continue

                # 8. Default Deny
                results.append(
                    EffectivePermissionRow(
                        id=f"{tbl_id}_{act.value}",
                        database_id=db_id,
                        database_name=row.db_name,
                        schema_id=sch_id,
                        schema_name=row.schema_name,
                        table_id=tbl_id,
                        table_name=row.table_name,
                        resource_type=ResourceType.TABLE,
                        resource_name=row.table_name,
                        permission=act,
                        result=PermissionEffect.DENY,
                        source_type="DEFAULT_DENY",
                        source_name="خط‌مشی پیش‌فرض (Default Deny)",
                        is_inherited=False,
                        rule_id=None,
                        explanation="هیچ قانون مجازکننده‌ای (ALLOW) به صورت مستقیم یا موروثی برای این موجودیت صادر نشده است.",
                    )
                )

        return results
