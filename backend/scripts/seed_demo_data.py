#!/usr/bin/env python3
"""
Enterprise Data Catalog - Comprehensive Idempotent Demo Data Seeder

Seeds a production-grade catalog database with:
- System and business roles (SuperAdmin, DataSteward, DataAnalyst)
- Enterprise users with secure Argon2id passwords
- Multi-dialect encrypted connections (PostgreSQL, Snowflake, MySQL)
- Hierarchical categories (Enterprise Core -> Customer Data, Financial & Payroll)
- Dual-dimension RBAC rules (Database/Schema ALLOW + Table explicit DENY)
- Rich catalog assets (Schemas, Tables, Columns with PII/Classification metadata)
- Standardized taxonomy tags (PII, Financial, PCI-DSS, Gold-Tier, GDPR)
- Historical table snapshots (v1, v2) and schema change logs
- Partitioned immutable audit log events
- Refreshed materialized views for executive analytics dashboards

Idempotency:
Safe to run repeatedly. Existing entities are detected by unique keys and updated or preserved.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is in sys.path so 'backend.src' resolves seamlessly
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.database import async_session_factory
from backend.src.models.audit import AuditLog
from backend.src.models.catalog import (
    CatalogColumn,
    CatalogTable,
    ClassificationLevel,
    DatabaseSchema,
    LifecycleStatus,
    TableType,
    Tag,
    TagAssignment,
)
from backend.src.models.connections import DatabaseConnection, DatabaseEngine, SyncSchedule
from backend.src.models.identity import Group, GroupRole, Role, User, UserGroup, UserRole, UserStatus
from backend.src.models.rbac import (
    Category,
    CategoryAssignment,
    EntityType,
    PermissionAction,
    PermissionEffect,
    PermissionRule,
    ResourceType,
)
from backend.src.models.versioning import (
    SchemaChangeLog,
    SchemaChangeType,
    TableVersion,
)
from backend.src.services.auth.jwt_service import auth_service
from backend.src.services.crypto.envelope import EnvelopeEncryptionService
from backend.src.services.reporting.report_service import report_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("seed_demo_data")


async def seed_all_demo_data() -> None:
    logger.info("Starting Enterprise Data Catalog demo data seeding...")
    crypto = EnvelopeEncryptionService()

    async with async_session_factory() as session:
        # =====================================================================
        # 1. ROLES
        # =====================================================================
        logger.info("Seeding roles...")
        roles_config = [
            {
                "name": "SuperAdmin",
                "description": "Unrestricted platform administrator with root access",
                "is_system_role": True,
            },
            {
                "name": "DataSteward",
                "description": "Data governance custodian authorized to curate metadata and classifications",
                "is_system_role": False,
            },
            {
                "name": "DataAnalyst",
                "description": "Read-only data consumer authorized to explore certified datasets",
                "is_system_role": False,
            },
        ]
        role_map: dict[str, Role] = {}
        for r_spec in roles_config:
            stmt = select(Role).where(Role.name == r_spec["name"])
            existing_role = (await session.execute(stmt)).scalar_one_or_none()
            if existing_role:
                existing_role.description = r_spec["description"]
                role_map[r_spec["name"]] = existing_role
            else:
                new_role = Role(
                    id=uuid.uuid4(),
                    name=r_spec["name"],
                    description=r_spec["description"],
                    is_system_role=r_spec["is_system_role"],
                )
                session.add(new_role)
                role_map[r_spec["name"]] = new_role
        await session.flush()

        # =====================================================================
        # 2. USERS
        # =====================================================================
        logger.info("Seeding users...")
        users_config = [
            {
                "username": "admin",
                "email": "admin@company.internal",
                "full_name": "مدیر ارشد سامانه (Chief Administrator)",
                "password": "Admin#2026",
                "phone": "09121000001",
                "is_superadmin": True,
                "role_names": ["SuperAdmin"],
            },
            {
                "username": "steward",
                "email": "steward@company.internal",
                "full_name": "راهبر و متولی داده (Senior Data Steward)",
                "password": "Steward#2026",
                "phone": "09121000002",
                "is_superadmin": False,
                "role_names": ["DataSteward"],
            },
            {
                "username": "analyst",
                "email": "analyst@company.internal",
                "full_name": "تحلیل‌گر داده و هوش تجاری (BI Analyst)",
                "password": "Analyst#2026",
                "phone": "09121000003",
                "is_superadmin": False,
                "role_names": ["DataAnalyst"],
            },
            {
                "username": "developer",
                "email": "developer@company.internal",
                "full_name": "مهندس ارشد زیرساخت و داده (DataOps & Pipelines)",
                "password": "Developer#2026",
                "phone": "09121000004",
                "is_superadmin": False,
                "role_names": ["DataSteward"],
            },
            {
                "username": "viewer",
                "email": "viewer@company.internal",
                "full_name": "تحلیل‌گر مهمان (Guest Analyst)",
                "password": "Viewer#2026",
                "phone": "09121000005",
                "is_superadmin": False,
                "role_names": ["DataAnalyst"],
            },
        ]
        user_map: dict[str, User] = {}
        for u_spec in users_config:
            stmt = select(User).where(User.username == u_spec["username"])
            existing_user = (await session.execute(stmt)).scalar_one_or_none()
            if existing_user:
                existing_user.full_name = u_spec["full_name"]
                existing_user.email = u_spec["email"]
                existing_user.phone = u_spec["phone"]
                existing_user.is_superadmin = u_spec["is_superadmin"]
                existing_user.hashed_password = auth_service.hash_password(u_spec["password"])
                user_map[u_spec["username"]] = existing_user
            else:
                new_user = User(
                    id=uuid.uuid4(),
                    username=u_spec["username"],
                    email=u_spec["email"],
                    full_name=u_spec["full_name"],
                    phone=u_spec["phone"],
                    hashed_password=auth_service.hash_password(u_spec["password"]),
                    status=UserStatus.ACTIVE,
                    is_superadmin=u_spec["is_superadmin"],
                )
                session.add(new_user)
                user_map[u_spec["username"]] = new_user
        await session.flush()

        # Assign user roles idempotently
        for u_spec in users_config:
            u_obj = user_map[u_spec["username"]]
            for r_name in u_spec["role_names"]:
                r_obj = role_map[r_name]
                ur_stmt = select(UserRole).where(
                    UserRole.user_id == u_obj.id, UserRole.role_id == r_obj.id
                )
                existing_ur = (await session.execute(ur_stmt)).scalar_one_or_none()
                if not existing_ur:
                    session.add(UserRole(user_id=u_obj.id, role_id=r_obj.id))
        await session.flush()

        # =====================================================================
        # 2.5. GROUPS & GROUP MEMBERSHIPS
        # =====================================================================
        logger.info("Seeding enterprise groups...")
        groups_config = [
            {
                "name": "تیم مهندسی داده (Data Engineering)",
                "description": "تیم مسئول خطوط داده، پایپ‌لاین‌های بلع و معماری ذخیره‌سازی داده‌های سازمانی",
                "roles": ["DataSteward"],
                "members": ["steward", "developer"],
            },
            {
                "name": "تیم هوش تجاری و تحلیل (BI & Analytics)",
                "description": "تیم تحلیل‌گران، دانشمندان داده و توسعه‌دهندگان داشبوردهای مدیریتی",
                "roles": ["DataAnalyst"],
                "members": ["analyst", "viewer"],
            },
            {
                "name": "راهبری و حاکمیت داده (Governance & Security)",
                "description": "کمیته ارشد امنیت اطلاعات، تدوین استانداردها و طبقه‌بندی محرمانگی داده‌ها",
                "roles": ["DataSteward"],
                "members": ["steward", "admin"],
            },
        ]
        group_map: dict[str, Group] = {}
        for g_spec in groups_config:
            stmt = select(Group).where(Group.name == g_spec["name"])
            existing_grp = (await session.execute(stmt)).scalar_one_or_none()
            if existing_grp:
                existing_grp.description = g_spec["description"]
                group_map[g_spec["name"]] = existing_grp
            else:
                new_grp = Group(
                    id=uuid.uuid4(),
                    name=g_spec["name"],
                    description=g_spec["description"],
                    is_active=True,
                )
                session.add(new_grp)
                group_map[g_spec["name"]] = new_grp
        await session.flush()

        # Group roles and members
        for g_spec in groups_config:
            g_obj = group_map[g_spec["name"]]
            for r_name in g_spec["roles"]:
                r_obj = role_map[r_name]
                gr_stmt = select(GroupRole).where(GroupRole.group_id == g_obj.id, GroupRole.role_id == r_obj.id)
                if not (await session.execute(gr_stmt)).scalar_one_or_none():
                    session.add(GroupRole(group_id=g_obj.id, role_id=r_obj.id, assigned_by=user_map["admin"].id))

            for m_username in g_spec["members"]:
                m_user = user_map[m_username]
                ug_stmt = select(UserGroup).where(UserGroup.group_id == g_obj.id, UserGroup.user_id == m_user.id)
                if not (await session.execute(ug_stmt)).scalar_one_or_none():
                    session.add(UserGroup(group_id=g_obj.id, user_id=m_user.id, assigned_by=user_map["admin"].id))
        await session.flush()

        # =====================================================================
        # 3. DATABASE CONNECTIONS (ENVELOPE ENCRYPTED)
        # =====================================================================
        logger.info("Seeding encrypted database connections...")
        connections_config = [
            {
                "name": "Analytics Data Warehouse",
                "engine_type": DatabaseEngine.POSTGRESQL,
                "host": "dw-postgres.internal.cloud",
                "port": 5432,
                "database_name": "dw_analytics",
                "default_schema": "analytics_public",
                "owner": "admin",
                "credentials": {
                    "username": "dw_admin",
                    "password": "DW_Prod_SecurePassword!#99",
                },
                "cron": "0 2 * * *",
            },
            {
                "name": "Customer 360 Lakehouse",
                "engine_type": DatabaseEngine.POSTGRESQL,
                "host": "lake-postgres.internal.cloud",
                "port": 5432,
                "database_name": "customer360_lake",
                "default_schema": "crm_profiles",
                "owner": "steward",
                "credentials": {
                    "username": "lake_reader",
                    "password": "Lake_Reader_SecretPass!#88",
                },
                "cron": "0 4 * * *",
            },
            {
                "name": "Core Banking Ledger",
                "engine_type": DatabaseEngine.POSTGRESQL,
                "host": "banking-db.internal.cloud",
                "port": 5432,
                "database_name": "banking_ledger",
                "default_schema": "core_accounts",
                "owner": "steward",
                "credentials": {
                    "username": "ledger_auditor",
                    "password": "Audit_Vault_SecretPass!#77",
                },
                "cron": "0 1 * * *",
            },
        ]
        connection_map: dict[str, DatabaseConnection] = {}
        for c_spec in connections_config:
            c_stmt = select(DatabaseConnection).where(DatabaseConnection.name == c_spec["name"])
            existing_conn = (await session.execute(c_stmt)).scalar_one_or_none()
            enc_dek, enc_creds = crypto.encrypt_credentials(c_spec["credentials"])

            if existing_conn:
                existing_conn.host = c_spec["host"]
                existing_conn.port = c_spec["port"]
                existing_conn.database_name = c_spec["database_name"]
                existing_conn.default_schema = c_spec["default_schema"]
                existing_conn.owner = c_spec["owner"]
                existing_conn.encrypted_dek = enc_dek
                existing_conn.encrypted_credentials = enc_creds
                connection_map[c_spec["name"]] = existing_conn
            else:
                new_conn = DatabaseConnection(
                    id=uuid.uuid4(),
                    name=c_spec["name"],
                    engine_type=c_spec["engine_type"],
                    host=c_spec["host"],
                    port=c_spec["port"],
                    database_name=c_spec["database_name"],
                    default_schema=c_spec["default_schema"],
                    owner=c_spec["owner"],
                    encrypted_dek=enc_dek,
                    encrypted_credentials=enc_creds,
                    is_active=True,
                )
                session.add(new_conn)
                connection_map[c_spec["name"]] = new_conn
        await session.flush()

        # Seed Sync Schedules for connections
        for c_spec in connections_config:
            conn_obj = connection_map[c_spec["name"]]
            sched_stmt = select(SyncSchedule).where(SyncSchedule.connection_id == conn_obj.id)
            existing_sched = (await session.execute(sched_stmt)).scalar_one_or_none()
            if existing_sched:
                existing_sched.cron_expression = c_spec["cron"]
            else:
                session.add(
                    SyncSchedule(
                        id=uuid.uuid4(),
                        connection_id=conn_obj.id,
                        cron_expression=c_spec["cron"],
                        is_enabled=True,
                    )
                )
        await session.flush()

        # =====================================================================
        # 4. TAXONOMY TAGS
        # =====================================================================
        logger.info("Seeding taxonomy tags...")
        tags_config = [
            {"name": "PII", "color": "#ef4444", "description": "Personally Identifiable Information"},
            {"name": "PCI-DSS", "color": "#dc2626", "description": "Payment Card Industry Regulated"},
            {"name": "Financial", "color": "#f59e0b", "description": "Material Financial Information"},
            {"name": "Gold-Tier", "color": "#eab308", "description": "Certified Enterprise Master Data Asset"},
            {"name": "GDPR-Article-9", "color": "#b91c1c", "description": "Special Category Personal Data"},
            {"name": "Core-Entity", "color": "#3b82f6", "description": "Fundamental Domain Model Entity"},
        ]
        tag_map: dict[str, Tag] = {}
        for t_spec in tags_config:
            t_stmt = select(Tag).where(Tag.name == t_spec["name"])
            existing_tag = (await session.execute(t_stmt)).scalar_one_or_none()
            if existing_tag:
                existing_tag.color = t_spec["color"]
                existing_tag.description = t_spec["description"]
                tag_map[t_spec["name"]] = existing_tag
            else:
                new_tag = Tag(
                    id=uuid.uuid4(),
                    name=t_spec["name"],
                    color=t_spec["color"],
                    description=t_spec["description"],
                )
                session.add(new_tag)
                tag_map[t_spec["name"]] = new_tag
        await session.flush()

        # =====================================================================
        # 5. CATEGORIES & HIERARCHY
        # =====================================================================
        logger.info("Seeding category hierarchy...")
        cat_stmt = select(Category).where(Category.name == "Enterprise Core")
        root_cat = (await session.execute(cat_stmt)).scalar_one_or_none()
        if not root_cat:
            root_cat = Category(
                id=uuid.uuid4(),
                name="Enterprise Core",
                description="Governed mission-critical enterprise data domains",
                parent_id=None,
                status="ACTIVE",
            )
            session.add(root_cat)
            await session.flush()

        # Child categories
        child_cats = [
            {"name": "Customer Data", "description": "Customer identities, contacts, and consent"},
            {"name": "Financial & Payroll", "description": "General ledger, payroll, and executive compensation"},
        ]
        cat_map: dict[str, Category] = {"Enterprise Core": root_cat}
        for c_spec in child_cats:
            stmt = select(Category).where(Category.name == c_spec["name"])
            existing_child = (await session.execute(stmt)).scalar_one_or_none()
            if existing_child:
                cat_map[c_spec["name"]] = existing_child
            else:
                new_child = Category(
                    id=uuid.uuid4(),
                    name=c_spec["name"],
                    description=c_spec["description"],
                    parent_id=root_cat.id,
                    status="ACTIVE",
                )
                session.add(new_child)
                cat_map[c_spec["name"]] = new_child
        await session.flush()

        # =====================================================================
        # 6. SCHEMAS, TABLES, AND COLUMNS
        # =====================================================================
        logger.info("Seeding schemas, tables, and columns...")
        schemas_config = [
            # DW Schemas
            {
                "connection": "Analytics Data Warehouse",
                "name": "analytics_public",
                "description": "Standard dimensional analytics schema",
                "tables": [
                    {
                        "name": "dim_customers",
                        "type": TableType.TABLE,
                        "source_desc": "شناسنامه اطلاعات مشتریان همگام شده از سامانه CRM / Customer profile dimension from CRM sync",
                        "approved_desc": "بعد جامع و پالایش شده مشتریان سازمانی با شناسه‌های یکتا و اعتبارسنجی شده / Master enterprise customer dimension with deduplicated identifiers",
                        "owner": "analytics_steward",
                        "row_count": 850000,
                        "size_bytes": 128000000,
                        "tags": ["Gold-Tier", "Core-Entity"],
                        "category": "Customer Data",
                        "columns": [
                            {"name": "customer_id", "pos": 1, "type": "BIGINT", "raw_type": "bigint", "pk": True, "fk": False, "null": False, "class": ClassificationLevel.INTERNAL, "s_desc": "کلید اصلی جایگزین / Surrogate primary key", "a_desc": "شناسه یکتای مشتری در انبار داده / Primary integer identifier", "tags": []},
                            {"name": "email", "pos": 2, "type": "VARCHAR(255)", "raw_type": "varchar(255)", "pk": False, "fk": False, "null": False, "class": ClassificationLevel.CONFIDENTIAL, "s_desc": "ایمیل اصلی مشتری / Customer primary email", "a_desc": "پست الکترونیکی تایید شده مشتری / Verified contact email address", "tags": ["PII"]},
                            {"name": "full_name", "pos": 3, "type": "VARCHAR(255)", "raw_type": "varchar(255)", "pk": False, "fk": False, "null": False, "class": ClassificationLevel.CONFIDENTIAL, "s_desc": "نام نمایشی مشتری / Customer display name", "a_desc": "نام و نام خانوادگی رسمی مشتری / Legal full name", "tags": ["PII"]},
                            {"name": "created_at", "pos": 4, "type": "TIMESTAMPTZ", "raw_type": "timestamptz", "pk": False, "fk": False, "null": False, "class": ClassificationLevel.INTERNAL, "s_desc": None, "a_desc": "زمان ثبت نام مشتری به وقت گرینویچ / UTC timestamp of user registration", "tags": []},
                            {"name": "is_active", "pos": 5, "type": "BOOLEAN", "raw_type": "boolean", "pk": False, "fk": False, "null": False, "class": ClassificationLevel.PUBLIC, "s_desc": "نشانگر وضعیت فعال / Account active flag", "a_desc": "وضعیت فعال بودن عضویت مشتری / Active membership status", "tags": []},
                        ],
                    },
                    {
                        "name": "fact_daily_orders",
                        "type": TableType.TABLE,
                        "source_desc": None,
                        "approved_desc": "تراکنش‌های تایید شده و مغایرت‌گیری شده مالی با درگاه‌های پرداخت / Certified enterprise order transactions reconciled against Stripe and Adyen",
                        "owner": "finance_team",
                        "row_count": 4200000,
                        "size_bytes": 940000000,
                        "tags": ["Gold-Tier", "Financial"],
                        "category": "Enterprise Core",
                        "columns": [
                            {"name": "order_id", "pos": 1, "type": "BIGINT", "raw_type": "bigint", "pk": True, "fk": False, "null": False, "class": ClassificationLevel.INTERNAL, "s_desc": "شناسه سفارش / Order unique identifier", "a_desc": "شناسه یکتای جهانی سفارش / Global order ID", "tags": []},
                            {"name": "customer_id", "pos": 2, "type": "BIGINT", "raw_type": "bigint", "pk": False, "fk": True, "null": False, "class": ClassificationLevel.INTERNAL, "s_desc": "کلید خارجی به مشتریان / Foreign key to dim_customers", "a_desc": "شناسه مشتری خریدار / Purchasing customer ID", "tags": []},
                            {"name": "order_total_cents", "pos": 3, "type": "NUMERIC(14,2)", "raw_type": "numeric(14,2)", "pk": False, "fk": False, "null": False, "class": ClassificationLevel.INTERNAL, "s_desc": "مبلغ ناخالص به سنت / Gross order amount in cents", "a_desc": "مجموع مالی به سنت دلار / Gross monetary total in USD cents", "tags": ["Financial"]},
                            {"name": "order_date", "pos": 4, "type": "DATE", "raw_type": "date", "pk": False, "fk": False, "null": False, "class": ClassificationLevel.INTERNAL, "s_desc": "تاریخ تقویمی تراکنش / Transaction calendar date", "a_desc": "تاریخ گزارش‌گیری تراکنش / Reporting transaction date", "tags": []},
                        ],
                    },
                ],
            },
            {
                "connection": "Analytics Data Warehouse",
                "name": "finance_restricted",
                "description": "High-security financial assets and executive compensation",
                "tables": [
                    {
                        "name": "salary_records",
                        "type": TableType.TABLE,
                        "source_desc": "داده‌های حقوق و دستمزد پرسنل / Workday payroll sync",
                        "approved_desc": "سوابق محرمانه حقوق و مزایای مدیران و پرسنل مشمول قوانین حاکمیتی / Confidential executive and employee payroll compensation records",
                        "owner": "hr_executive",
                        "row_count": 4500,
                        "size_bytes": 2400000,
                        "tags": ["PII", "Financial", "GDPR-Article-9"],
                        "category": "Financial & Payroll",
                        "columns": [
                            {"name": "employee_id", "pos": 1, "type": "BIGINT", "raw_type": "bigint", "pk": True, "fk": False, "null": False, "class": ClassificationLevel.CONFIDENTIAL, "s_desc": "کد شناسایی پرسنلی / Employee badge identifier", "a_desc": "شناسه یکتای کارمند سازمانی / Unique corporate employee identifier", "tags": []},
                            {"name": "base_salary_cents", "pos": 2, "type": "NUMERIC(14,2)", "raw_type": "numeric(14,2)", "pk": False, "fk": False, "null": False, "class": ClassificationLevel.CONFIDENTIAL, "s_desc": "حقوق پایه سالانه به سنت / Annual base compensation in cents", "a_desc": "حقوق پایه مصوب سالیانه / Annualized base salary", "tags": ["Financial"]},
                            {"name": "bonus_target_cents", "pos": 3, "type": "NUMERIC(14,2)", "raw_type": "numeric(14,2)", "pk": False, "fk": False, "null": True, "class": ClassificationLevel.CONFIDENTIAL, "s_desc": "تارگت پاداش / Discretionary incentive target", "a_desc": "پاداش هدف سالانه مصوب / Target annual bonus", "tags": ["Financial"]},
                            {"name": "currency", "pos": 4, "type": "VARCHAR(3)", "raw_type": "varchar(3)", "pk": False, "fk": False, "null": False, "class": ClassificationLevel.INTERNAL, "s_desc": "کد استاندارد ارز / ISO currency symbol", "a_desc": "کد ۳ حرفی استاندارد ارز ISO 4217 / ISO 4217 3-letter currency code", "tags": []},
                        ],
                    }
                ],
            },
            # Core Banking Schemas
            {
                "connection": "Core Banking Ledger",
                "name": "core_accounts",
                "description": "Real-time ledger accounts and wire transactions",
                "tables": [
                    {
                        "name": "wire_transactions",
                        "type": TableType.TABLE,
                        "source_desc": "تراکنش‌های تسویه بین بانکی ساتنا و پایا / SWIFT and Fedwire clearing transactions",
                        "approved_desc": "تراکنش‌های قطعی و تغییرناپذیر دفترکل بانکی مشمول ممیزی / Immutable bank ledger wire movements subject to federal audit",
                        "owner": "compliance_lead",
                        "row_count": 1200000,
                        "size_bytes": 450000000,
                        "tags": ["PCI-DSS", "Financial", "Gold-Tier"],
                        "category": "Financial & Payroll",
                        "columns": [
                            {"name": "wire_id", "pos": 1, "type": "UUID", "raw_type": "uuid", "pk": True, "fk": False, "null": False, "class": ClassificationLevel.CONFIDENTIAL, "s_desc": "کد رهگیری حواله بانکی / SWIFT wire reference ID", "a_desc": "کد مرجع پیام حواله بانکی / SWIFT message reference", "tags": []},
                            {"name": "originating_account", "pos": 2, "type": "VARCHAR(34)", "raw_type": "varchar(34)", "pk": False, "fk": False, "null": False, "class": ClassificationLevel.CONFIDENTIAL, "s_desc": "شماره حساب یا شبا مبدا / Originating IBAN/account", "a_desc": "شماره شبای فرستنده / Sender IBAN number", "tags": ["PCI-DSS"]},
                            {"name": "amount", "pos": 3, "type": "NUMERIC(18,2)", "raw_type": "numeric(18,2)", "pk": False, "fk": False, "null": False, "class": ClassificationLevel.CONFIDENTIAL, "s_desc": "مبلغ تراکنش حواله / Transfer amount in transaction currency", "a_desc": "مبلغ اسمی انتقال بانکی / Notional wire transaction amount", "tags": ["Financial"]},
                        ],
                    }
                ],
            },
        ]

        table_map: dict[str, CatalogTable] = {}

        for s_spec in schemas_config:
            conn_obj = connection_map[s_spec["connection"]]
            # Upsert schema
            s_stmt = select(DatabaseSchema).where(
                DatabaseSchema.connection_id == conn_obj.id,
                DatabaseSchema.name == s_spec["name"],
            )
            existing_schema = (await session.execute(s_stmt)).scalar_one_or_none()
            if existing_schema:
                existing_schema.description = s_spec["description"]
                schema_obj = existing_schema
            else:
                schema_obj = DatabaseSchema(
                    id=uuid.uuid4(),
                    connection_id=conn_obj.id,
                    name=s_spec["name"],
                    description=s_spec["description"],
                    status=LifecycleStatus.ACTIVE,
                )
                session.add(schema_obj)
            await session.flush()

            for t_spec in s_spec["tables"]:
                # Upsert table
                t_stmt = select(CatalogTable).where(
                    CatalogTable.schema_id == schema_obj.id,
                    CatalogTable.name == t_spec["name"],
                )
                existing_table = (await session.execute(t_stmt)).scalar_one_or_none()
                if existing_table:
                    existing_table.source_description = t_spec["source_desc"]
                    existing_table.approved_description = t_spec["approved_desc"]
                    existing_table.owner = t_spec["owner"]
                    existing_table.row_count_estimate = t_spec["row_count"]
                    existing_table.size_bytes = t_spec["size_bytes"]
                    table_obj = existing_table
                else:
                    table_obj = CatalogTable(
                        id=uuid.uuid4(),
                        schema_id=schema_obj.id,
                        name=t_spec["name"],
                        table_type=t_spec["type"],
                        source_description=t_spec["source_desc"],
                        approved_description=t_spec["approved_desc"],
                        owner=t_spec["owner"],
                        row_count_estimate=t_spec["row_count"],
                        size_bytes=t_spec["size_bytes"],
                        status=LifecycleStatus.ACTIVE,
                    )
                    session.add(table_obj)
                await session.flush()
                table_map[t_spec["name"]] = table_obj

                # Assign table category
                if t_spec.get("category") and t_spec["category"] in cat_map:
                    cat_obj = cat_map[t_spec["category"]]
                    ca_stmt = select(CategoryAssignment).where(
                        CategoryAssignment.category_id == cat_obj.id,
                        CategoryAssignment.entity_type == EntityType.TABLE,
                        CategoryAssignment.entity_id == table_obj.id,
                    )
                    existing_ca = (await session.execute(ca_stmt)).scalar_one_or_none()
                    if not existing_ca:
                        session.add(
                            CategoryAssignment(
                                id=uuid.uuid4(),
                                category_id=cat_obj.id,
                                entity_type=EntityType.TABLE,
                                entity_id=table_obj.id,
                            )
                        )

                # Assign table tags
                for tag_name in t_spec.get("tags", []):
                    if tag_name in tag_map:
                        t_tag = tag_map[tag_name]
                        ta_stmt = select(TagAssignment).where(
                            TagAssignment.tag_id == t_tag.id,
                            TagAssignment.entity_type == EntityType.TABLE,
                            TagAssignment.entity_id == table_obj.id,
                        )
                        existing_ta = (await session.execute(ta_stmt)).scalar_one_or_none()
                        if not existing_ta:
                            session.add(
                                TagAssignment(
                                    id=uuid.uuid4(),
                                    tag_id=t_tag.id,
                                    entity_type=EntityType.TABLE,
                                    entity_id=table_obj.id,
                                )
                            )

                # Upsert columns
                for col_spec in t_spec["columns"]:
                    col_stmt = select(CatalogColumn).where(
                        CatalogColumn.table_id == table_obj.id,
                        CatalogColumn.name == col_spec["name"],
                    )
                    existing_col = (await session.execute(col_stmt)).scalar_one_or_none()
                    if existing_col:
                        existing_col.ordinal_position = col_spec["pos"]
                        existing_col.data_type = col_spec["type"]
                        existing_col.raw_data_type = col_spec["raw_type"]
                        existing_col.is_primary_key = col_spec["pk"]
                        existing_col.is_foreign_key = col_spec["fk"]
                        existing_col.is_nullable = col_spec["null"]
                        existing_col.classification_level = col_spec["class"]
                        existing_col.source_description = col_spec["s_desc"]
                        existing_col.approved_description = col_spec["a_desc"]
                        col_obj = existing_col
                    else:
                        col_obj = CatalogColumn(
                            id=uuid.uuid4(),
                            table_id=table_obj.id,
                            name=col_spec["name"],
                            ordinal_position=col_spec["pos"],
                            data_type=col_spec["type"],
                            raw_data_type=col_spec["raw_type"],
                            is_primary_key=col_spec["pk"],
                            is_foreign_key=col_spec["fk"],
                            is_nullable=col_spec["null"],
                            classification_level=col_spec["class"],
                            source_description=col_spec["s_desc"],
                            approved_description=col_spec["a_desc"],
                            status=LifecycleStatus.ACTIVE,
                        )
                        session.add(col_obj)
                    await session.flush()

                    # Assign column tags
                    for c_tag_name in col_spec.get("tags", []):
                        if c_tag_name in tag_map:
                            c_tag = tag_map[c_tag_name]
                            cta_stmt = select(TagAssignment).where(
                                TagAssignment.tag_id == c_tag.id,
                                TagAssignment.entity_type == EntityType.COLUMN,
                                TagAssignment.entity_id == col_obj.id,
                            )
                            existing_cta = (await session.execute(cta_stmt)).scalar_one_or_none()
                            if not existing_cta:
                                session.add(
                                    TagAssignment(
                                        id=uuid.uuid4(),
                                        tag_id=c_tag.id,
                                        entity_type=EntityType.COLUMN,
                                        entity_id=col_obj.id,
                                    )
                                )
        await session.flush()

        # =====================================================================
        # 7. DUAL-DIMENSION RBAC PERMISSION RULES
        # =====================================================================
        logger.info("Seeding dual-dimension RBAC permission rules...")
        # 1. DataSteward: Full Curation & View Metadata on root Enterprise Core category
        rule1_stmt = select(PermissionRule).where(
            PermissionRule.role_id == role_map["DataSteward"].id,
            PermissionRule.resource_type == ResourceType.CATEGORY,
            PermissionRule.resource_id == root_cat.id,
            PermissionRule.action == PermissionAction.VIEW_METADATA,
        )
        if not (await session.execute(rule1_stmt)).scalar_one_or_none():
            session.add(
                PermissionRule(
                    id=uuid.uuid4(),
                    role_id=role_map["DataSteward"].id,
                    resource_type=ResourceType.CATEGORY,
                    resource_id=root_cat.id,
                    action=PermissionAction.VIEW_METADATA,
                    effect=PermissionEffect.ALLOW,
                )
            )

        # 2. DataAnalyst: ALLOW VIEW_METADATA on Customer Data category
        cust_cat = cat_map["Customer Data"]
        rule2_stmt = select(PermissionRule).where(
            PermissionRule.role_id == role_map["DataAnalyst"].id,
            PermissionRule.resource_type == ResourceType.CATEGORY,
            PermissionRule.resource_id == cust_cat.id,
            PermissionRule.action == PermissionAction.VIEW_METADATA,
        )
        if not (await session.execute(rule2_stmt)).scalar_one_or_none():
            session.add(
                PermissionRule(
                    id=uuid.uuid4(),
                    role_id=role_map["DataAnalyst"].id,
                    resource_type=ResourceType.CATEGORY,
                    resource_id=cust_cat.id,
                    action=PermissionAction.VIEW_METADATA,
                    effect=PermissionEffect.ALLOW,
                )
            )

        # 3. DataAnalyst: Explicit DENY VIEW_METADATA on salary_records table
        salary_tbl = table_map.get("salary_records")
        if salary_tbl:
            rule3_stmt = select(PermissionRule).where(
                PermissionRule.role_id == role_map["DataAnalyst"].id,
                PermissionRule.resource_type == ResourceType.TABLE,
                PermissionRule.resource_id == salary_tbl.id,
                PermissionRule.action == PermissionAction.VIEW_METADATA,
            )
            if not (await session.execute(rule3_stmt)).scalar_one_or_none():
                session.add(
                    PermissionRule(
                        id=uuid.uuid4(),
                        role_id=role_map["DataAnalyst"].id,
                        resource_type=ResourceType.TABLE,
                        resource_id=salary_tbl.id,
                        action=PermissionAction.VIEW_METADATA,
                        effect=PermissionEffect.DENY,
                    )
                )

        # 4. Group Permission: BI & Analytics Group ALLOW VIEW_METADATA on Customer 360 Lakehouse
        lake_conn = connection_map.get("Customer 360 Lakehouse")
        bi_grp = group_map.get("تیم هوش تجاری و تحلیل (BI & Analytics)")
        if lake_conn and bi_grp:
            grp_rule_stmt = select(PermissionRule).where(
                PermissionRule.group_id == bi_grp.id,
                PermissionRule.resource_type == ResourceType.DATABASE,
                PermissionRule.resource_id == lake_conn.id,
                PermissionRule.action == PermissionAction.VIEW_METADATA,
            )
            if not (await session.execute(grp_rule_stmt)).scalar_one_or_none():
                session.add(
                    PermissionRule(
                        id=uuid.uuid4(),
                        group_id=bi_grp.id,
                        resource_type=ResourceType.DATABASE,
                        resource_id=lake_conn.id,
                        action=PermissionAction.VIEW_METADATA,
                        effect=PermissionEffect.ALLOW,
                    )
                )

        # 5. Direct User Permission: developer user ALLOW EDIT_DESCRIPTION on dim_customers table
        dev_user = user_map.get("developer")
        dim_cust_tbl = table_map.get("dim_customers")
        if dev_user and dim_cust_tbl:
            dev_rule_stmt = select(PermissionRule).where(
                PermissionRule.user_id == dev_user.id,
                PermissionRule.resource_type == ResourceType.TABLE,
                PermissionRule.resource_id == dim_cust_tbl.id,
                PermissionRule.action == PermissionAction.EDIT_DESCRIPTION,
            )
            if not (await session.execute(dev_rule_stmt)).scalar_one_or_none():
                session.add(
                    PermissionRule(
                        id=uuid.uuid4(),
                        user_id=dev_user.id,
                        resource_type=ResourceType.TABLE,
                        resource_id=dim_cust_tbl.id,
                        action=PermissionAction.EDIT_DESCRIPTION,
                        effect=PermissionEffect.ALLOW,
                    )
                )
        await session.flush()

        # =====================================================================
        # 8. TABLE VERSIONS & SCHEMA CHANGE LOGS
        # =====================================================================
        logger.info("Seeding historical table snapshot versions...")
        dim_cust = table_map.get("dim_customers")
        if dim_cust:
            v1_cols = [
                {
                    "name": "customer_id",
                    "ordinal_position": 1,
                    "data_type": "BIGINT",
                    "raw_data_type": "bigint",
                    "is_nullable": False,
                    "is_primary_key": True,
                    "is_foreign_key": False,
                    "default_value": None,
                    "classification_level": "INTERNAL",
                },
                {
                    "name": "email",
                    "ordinal_position": 2,
                    "data_type": "VARCHAR(255)",
                    "raw_data_type": "varchar(255)",
                    "is_nullable": False,
                    "is_primary_key": False,
                    "is_foreign_key": False,
                    "default_value": None,
                    "classification_level": "CONFIDENTIAL",
                },
                {
                    "name": "full_name",
                    "ordinal_position": 3,
                    "data_type": "VARCHAR(255)",
                    "raw_data_type": "varchar(255)",
                    "is_nullable": False,
                    "is_primary_key": False,
                    "is_foreign_key": False,
                    "default_value": None,
                    "classification_level": "CONFIDENTIAL",
                },
            ]

            v2_cols = [
                *v1_cols,
                {
                    "name": "created_at",
                    "ordinal_position": 4,
                    "data_type": "TIMESTAMPTZ",
                    "raw_data_type": "timestamptz",
                    "is_nullable": False,
                    "is_primary_key": False,
                    "is_foreign_key": False,
                    "default_value": "now()",
                    "classification_level": "INTERNAL",
                },
                {
                    "name": "is_active",
                    "ordinal_position": 5,
                    "data_type": "BOOLEAN",
                    "raw_data_type": "boolean",
                    "is_nullable": False,
                    "is_primary_key": False,
                    "is_foreign_key": False,
                    "default_value": "true",
                    "classification_level": "PUBLIC",
                },
            ]

            v3_cols = [
                *v2_cols,
                {
                    "name": "loyalty_tier",
                    "ordinal_position": 6,
                    "data_type": "VARCHAR(50)",
                    "raw_data_type": "varchar(50)",
                    "is_nullable": True,
                    "is_primary_key": False,
                    "is_foreign_key": False,
                    "default_value": "'BRONZE'",
                    "classification_level": "INTERNAL",
                },
            ]

            # Check if version 1 exists
            v1_stmt = select(TableVersion).where(
                TableVersion.table_id == dim_cust.id, TableVersion.version_num == 1
            )
            v1_obj = (await session.execute(v1_stmt)).scalar_one_or_none()
            if not v1_obj:
                v1_obj = TableVersion(
                    id=uuid.uuid4(),
                    table_id=dim_cust.id,
                    version_num=1,
                    snapshot_jsonb={"columns": v1_cols},
                    created_by=user_map["admin"].id,
                )
                session.add(v1_obj)
            else:
                v1_obj.snapshot_jsonb = {"columns": v1_cols}
            await session.flush()

            # Version 2
            v2_stmt = select(TableVersion).where(
                TableVersion.table_id == dim_cust.id, TableVersion.version_num == 2
            )
            v2_obj = (await session.execute(v2_stmt)).scalar_one_or_none()
            if not v2_obj:
                v2_obj = TableVersion(
                    id=uuid.uuid4(),
                    table_id=dim_cust.id,
                    version_num=2,
                    snapshot_jsonb={"columns": v2_cols},
                    created_by=user_map["steward"].id,
                )
                session.add(v2_obj)
                await session.flush()

                session.add(
                    SchemaChangeLog(
                        id=uuid.uuid4(),
                        table_id=dim_cust.id,
                        version_id=v2_obj.id,
                        change_type=SchemaChangeType.COLUMN_ADDED,
                        column_name="created_at",
                        old_value=None,
                        new_value={"data_type": "TIMESTAMPTZ"},
                        actor_id=user_map["steward"].id,
                    )
                )
            else:
                v2_obj.snapshot_jsonb = {"columns": v2_cols}
            await session.flush()

            # Version 3
            v3_stmt = select(TableVersion).where(
                TableVersion.table_id == dim_cust.id, TableVersion.version_num == 3
            )
            v3_obj = (await session.execute(v3_stmt)).scalar_one_or_none()
            if not v3_obj:
                v3_obj = TableVersion(
                    id=uuid.uuid4(),
                    table_id=dim_cust.id,
                    version_num=3,
                    snapshot_jsonb={"columns": v3_cols},
                    created_by=user_map["admin"].id,
                )
                session.add(v3_obj)
                await session.flush()

                session.add(
                    SchemaChangeLog(
                        id=uuid.uuid4(),
                        table_id=dim_cust.id,
                        version_id=v3_obj.id,
                        change_type=SchemaChangeType.COLUMN_ADDED,
                        column_name="loyalty_tier",
                        old_value=None,
                        new_value={"data_type": "VARCHAR(50)"},
                        actor_id=user_map["admin"].id,
                    )
                )
            else:
                v3_obj.snapshot_jsonb = {"columns": v3_cols}
            await session.flush()
        await session.flush()

        # =====================================================================
        # 9. PARTITIONED AUDIT LOGS
        # =====================================================================
        logger.info("Seeding partitioned immutable audit trail...")
        audit_events = [
            {
                "actor": "admin",
                "action": "DATABASE_REGISTERED",
                "entity_type": EntityType.DATABASE,
                "entity_id": connection_map["Analytics Data Warehouse"].id,
                "diff": {"connection_name": "Analytics Data Warehouse", "engine": "POSTGRESQL"},
            },
            {
                "actor": "steward",
                "action": "EDIT_DESCRIPTION",
                "entity_type": EntityType.TABLE,
                "entity_id": table_map["dim_customers"].id,
                "diff": {
                    "field": "approved_description",
                    "old_value": None,
                    "new_value": table_map["dim_customers"].approved_description,
                },
            },
            {
                "actor": "steward",
                "action": "TAG_ASSIGNED",
                "entity_type": EntityType.TABLE,
                "entity_id": table_map["dim_customers"].id,
                "diff": {"tag": "Gold-Tier", "assigned_to": "dim_customers"},
            },
        ]
        for a_event in audit_events:
            session.add(
                AuditLog(
                    id=uuid.uuid4(),
                    created_at=datetime.now(timezone.utc),
                    actor_username=a_event["actor"],
                    actor_id=user_map[a_event["actor"]].id,
                    action=a_event["action"],
                    entity_type=a_event["entity_type"],
                    entity_id=a_event["entity_id"],
                    diff_jsonb=a_event["diff"],
                )
            )

        # Commit all core transactional entities before refreshing materialized views
        await session.commit()

    # =====================================================================
    # 10. REFRESH MATERIALIZED VIEWS
    # =====================================================================
    logger.info("Refreshing PostgreSQL reporting materialized views concurrently...")
    refreshed = await report_service.refresh_all_materialized_views_concurrently()
    for view_name, status in refreshed.items():
        logger.info("Refreshed materialized view '%s': %s", view_name, status)

    logger.info("=====================================================================")
    logger.info("Enterprise Data Catalog Demo Seeding Completed Successfully!")
    logger.info("Demo Login Credentials:")
    logger.info("  SuperAdmin: username='admin'   password='Admin#2026'")
    logger.info("  Steward:    username='steward' password='Steward#2026'")
    logger.info("  Analyst:    username='analyst' password='Analyst#2026'")
    logger.info("=====================================================================")


if __name__ == "__main__":
    asyncio.run(seed_all_demo_data())
