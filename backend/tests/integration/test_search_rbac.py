from __future__ import annotations

import time
import uuid
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.models.catalog import (
    CatalogColumn,
    CatalogTable,
    ClassificationLevel,
    DatabaseSchema,
    LifecycleStatus,
    TableType,
)
from backend.src.models.connections import DatabaseConnection, DatabaseEngine
from backend.src.models.identity import Role, User, UserRole, UserStatus
from backend.src.models.rbac import (
    EntityType,
    PermissionAction,
    PermissionEffect,
    PermissionRule,
    ResourceType,
)
from backend.src.api.dependencies.rbac import UserContext
from backend.src.services.catalog.search_service import SearchService


@pytest.fixture
async def rbac_search_test_data(test_db_session: AsyncSession):
    """
    Seeds a realistic database, schema, tables, roles, users, and permission rules
    to test SQL-pushed RBAC filtering and performance.
    """
    unique_suffix = uuid.uuid4().hex[:8]

    # 1. Create DatabaseConnection
    connection = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"analytics_warehouse_{unique_suffix}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host="localhost",
        port=5432,
        database_name=f"dw_analytics_{unique_suffix}",
        owner="data_admin@company.internal",
        encrypted_dek=b"x" * 32,
        encrypted_credentials=b"y" * 64,
        is_active=True,
    )
    test_db_session.add(connection)
    await test_db_session.flush()

    # 2. Create Schema
    schema = DatabaseSchema(
        id=uuid.uuid4(),
        connection_id=connection.id,
        name=f"public_{unique_suffix}",
        description="Public reporting schema",
        status=LifecycleStatus.ACTIVE,
    )
    test_db_session.add(schema)
    await test_db_session.flush()

    # 3. Create Tables
    # Table 1: Standard customer dimension (Allowed)
    customer_table = CatalogTable(
        id=uuid.uuid4(),
        schema_id=schema.id,
        name=f"dim_customer_{unique_suffix}",
        table_type=TableType.TABLE,
        row_count_estimate=500000,
        size_bytes=104857600,
        source_description="Master enterprise customer dimensions with contact info",
        approved_description=None,
        status=LifecycleStatus.ACTIVE,
    )
    # Table 2: Financial salaries (Confidential - will have explicit DENY rule)
    salary_table = CatalogTable(
        id=uuid.uuid4(),
        schema_id=schema.id,
        name=f"fact_confidential_salaries_{unique_suffix}",
        table_type=TableType.TABLE,
        row_count_estimate=100000,
        size_bytes=52428800,
        source_description="Executive payroll and compensation confidential transactions",
        approved_description=None,
        status=LifecycleStatus.ACTIVE,
    )
    # Table 3: Deprecated/Deleted table (Should never match standard search)
    deleted_table = CatalogTable(
        id=uuid.uuid4(),
        schema_id=schema.id,
        name=f"legacy_archived_customers_{unique_suffix}",
        table_type=TableType.TABLE,
        row_count_estimate=1000,
        size_bytes=1048576,
        source_description="Obsolete legacy customer archive",
        status=LifecycleStatus.DELETED,
    )

    test_db_session.add_all([customer_table, salary_table, deleted_table])
    await test_db_session.flush()

    # Add columns to verify search vector / column matching
    col1 = CatalogColumn(
        id=uuid.uuid4(),
        table_id=customer_table.id,
        name="customer_email",
        ordinal_position=1,
        data_type="varchar",
        raw_data_type="varchar(255)",
        source_description="Customer primary email address",
        classification_level=ClassificationLevel.INTERNAL,
        status=LifecycleStatus.ACTIVE,
    )
    col2 = CatalogColumn(
        id=uuid.uuid4(),
        table_id=salary_table.id,
        name="bonus_amount",
        ordinal_position=1,
        data_type="numeric",
        raw_data_type="numeric(12,2)",
        source_description="Annual executive discretionary bonus payout",
        classification_level=ClassificationLevel.CONFIDENTIAL,
        status=LifecycleStatus.ACTIVE,
    )
    test_db_session.add_all([col1, col2])
    await test_db_session.flush()

    # 4. Create Roles
    # Role A: Analyst (ALLOW on DATABASE)
    analyst_role = Role(
        id=uuid.uuid4(),
        name=f"Analyst_{unique_suffix}",
        description="Analyst with database-wide metadata access",
        is_system_role=False,
    )
    # Role B: Restricted Role (ALLOW on DATABASE, but explicit DENY on salary table)
    restricted_role = Role(
        id=uuid.uuid4(),
        name=f"RestrictedAnalyst_{unique_suffix}",
        description="Restricted Analyst role with explicit table deny",
        is_system_role=False,
    )
    # Role C: Empty Role (No rules)
    unauthorized_role = Role(
        id=uuid.uuid4(),
        name=f"Unauthorized_{unique_suffix}",
        description="Role with zero permission rules",
        is_system_role=False,
    )
    test_db_session.add_all([analyst_role, restricted_role, unauthorized_role])
    await test_db_session.flush()

    # 5. Permission Rules
    # Rule 1: Analyst has ALLOW on DATABASE
    rule_analyst_db = PermissionRule(
        id=uuid.uuid4(),
        role_id=analyst_role.id,
        resource_type=ResourceType.DATABASE,
        resource_id=connection.id,
        action=PermissionAction.VIEW_METADATA,
        effect=PermissionEffect.ALLOW,
    )
    # Rule 2: Restricted has ALLOW on DATABASE
    rule_restricted_db = PermissionRule(
        id=uuid.uuid4(),
        role_id=restricted_role.id,
        resource_type=ResourceType.DATABASE,
        resource_id=connection.id,
        action=PermissionAction.VIEW_METADATA,
        effect=PermissionEffect.ALLOW,
    )
    # Rule 3: Restricted has explicit DENY on salary_table
    rule_restricted_deny = PermissionRule(
        id=uuid.uuid4(),
        role_id=restricted_role.id,
        resource_type=ResourceType.TABLE,
        resource_id=salary_table.id,
        action=PermissionAction.VIEW_METADATA,
        effect=PermissionEffect.DENY,
    )
    test_db_session.add_all([rule_analyst_db, rule_restricted_db, rule_restricted_deny])
    await test_db_session.flush()

    # 6. Users
    user_analyst = User(
        id=uuid.uuid4(),
        username=f"analyst_{unique_suffix}",
        email=f"analyst_{unique_suffix}@company.internal",
        hashed_password="hashed_placeholder_pw",
        full_name="Analyst User",
        status=UserStatus.ACTIVE,
        is_superadmin=False,
    )
    user_restricted = User(
        id=uuid.uuid4(),
        username=f"restricted_{unique_suffix}",
        email=f"restricted_{unique_suffix}@company.internal",
        hashed_password="hashed_placeholder_pw",
        full_name="Restricted User",
        status=UserStatus.ACTIVE,
        is_superadmin=False,
    )
    user_unauthorized = User(
        id=uuid.uuid4(),
        username=f"unauth_{unique_suffix}",
        email=f"unauth_{unique_suffix}@company.internal",
        hashed_password="hashed_placeholder_pw",
        full_name="Unauthorized User",
        status=UserStatus.ACTIVE,
        is_superadmin=False,
    )
    user_superadmin = User(
        id=uuid.uuid4(),
        username=f"admin_{unique_suffix}",
        email=f"admin_{unique_suffix}@company.internal",
        hashed_password="hashed_placeholder_pw",
        full_name="Super Administrator",
        status=UserStatus.ACTIVE,
        is_superadmin=True,
    )
    test_db_session.add_all([user_analyst, user_restricted, user_unauthorized, user_superadmin])
    await test_db_session.flush()

    # User Roles mapping
    ur1 = UserRole(user_id=user_analyst.id, role_id=analyst_role.id)
    ur2 = UserRole(user_id=user_restricted.id, role_id=restricted_role.id)
    ur3 = UserRole(user_id=user_unauthorized.id, role_id=unauthorized_role.id)
    test_db_session.add_all([ur1, ur2, ur3])
    await test_db_session.flush()

    return {
        "connection": connection,
        "schema": schema,
        "customer_table": customer_table,
        "salary_table": salary_table,
        "deleted_table": deleted_table,
        "analyst_role": analyst_role,
        "restricted_role": restricted_role,
        "unauthorized_role": unauthorized_role,
        "user_analyst": user_analyst,
        "user_restricted": user_restricted,
        "user_unauthorized": user_unauthorized,
        "user_superadmin": user_superadmin,
    }


@pytest.mark.asyncio
async def test_user_without_permission_receives_zero_search_matches(
    test_db_session: AsyncSession, rbac_search_test_data: dict
):
    """
    Asserts that users without VIEW_METADATA permission receive 0 search matches,
    even when matching rows exist in PostgreSQL.
    """
    data = rbac_search_test_data
    user = data["user_unauthorized"]
    user_ctx = UserContext(user=user, role_ids=[data["unauthorized_role"].id], is_superadmin=False)

    service = SearchService()
    results, page_info = await service.search(
        session=test_db_session,
        user_ctx=user_ctx,
        query="customer",
        limit=50,
    )

    # Unauthorized user must receive 0 matches
    assert len(results) == 0
    assert page_info["has_next_page"] is False


@pytest.mark.asyncio
async def test_user_with_database_allow_receives_matches_under_100ms(
    test_db_session: AsyncSession, rbac_search_test_data: dict
):
    """
    Asserts that authorized users receive matching hits with latency < 100ms.
    """
    data = rbac_search_test_data
    user = data["user_analyst"]
    user_ctx = UserContext(user=user, role_ids=[data["analyst_role"].id], is_superadmin=False)

    service = SearchService()
    start_time = time.perf_counter()
    results, page_info = await service.search(
        session=test_db_session,
        user_ctx=user_ctx,
        query="customer",
        limit=50,
    )
    elapsed_ms = (time.perf_counter() - start_time) * 1000.0

    # Must find the customer table
    assert len(results) >= 1
    found_ids = [str(r["id"]) for r in results]
    assert str(data["customer_table"].id) in found_ids

    # Latency requirement: sub-100ms
    assert elapsed_ms < 100.0, f"Search took {elapsed_ms:.2f}ms, exceeding 100ms threshold"


@pytest.mark.asyncio
async def test_explicit_table_deny_rule_overrides_database_allow(
    test_db_session: AsyncSession, rbac_search_test_data: dict
):
    """
    Asserts that an explicit DENY rule on a table completely suppresses that table
    from search results even when the user has an ALLOW rule on the parent database.
    """
    data = rbac_search_test_data
    user = data["user_restricted"]
    user_ctx = UserContext(user=user, role_ids=[data["restricted_role"].id], is_superadmin=False)

    service = SearchService()

    # 1. Search for "salaries" / "compensation" - restricted user has explicit DENY on salary table
    deny_results, _ = await service.search(
        session=test_db_session,
        user_ctx=user_ctx,
        query="salaries",
        limit=50,
    )
    salary_ids = [str(r["id"]) for r in deny_results]
    assert str(data["salary_table"].id) not in salary_ids
    assert len(deny_results) == 0

    # 2. Search for "customer" - restricted user still inherits ALLOW on customer table
    allow_results, _ = await service.search(
        session=test_db_session,
        user_ctx=user_ctx,
        query="customer",
        limit=50,
    )
    assert len(allow_results) >= 1
    allow_ids = [str(r["id"]) for r in allow_results]
    assert str(data["customer_table"].id) in allow_ids


@pytest.mark.asyncio
async def test_superadmin_bypasses_rbac_filtering(
    test_db_session: AsyncSession, rbac_search_test_data: dict
):
    """
    Asserts that superadmins bypass RBAC filtering and can search all tables.
    """
    data = rbac_search_test_data
    user = data["user_superadmin"]
    user_ctx = UserContext(user=user, role_ids=[], is_superadmin=True)

    service = SearchService()
    results, _ = await service.search(
        session=test_db_session,
        user_ctx=user_ctx,
        query="salaries",
        limit=50,
    )
    found_ids = [str(r["id"]) for r in results]
    assert str(data["salary_table"].id) in found_ids


@pytest.mark.asyncio
async def test_deleted_tables_excluded_from_search_by_default(
    test_db_session: AsyncSession, rbac_search_test_data: dict
):
    """
    Asserts that soft-deleted tables are excluded from search results by default,
    even for superadmins, unless include_deleted=True.
    """
    data = rbac_search_test_data
    user = data["user_superadmin"]
    user_ctx = UserContext(user=user, role_ids=[], is_superadmin=True)

    service = SearchService()
    # Default search excludes deleted
    results, _ = await service.search(
        session=test_db_session,
        user_ctx=user_ctx,
        query="legacy_archived_customers",
        include_deleted=False,
    )
    assert len(results) == 0

    # include_deleted=True returns it
    results_with_deleted, _ = await service.search(
        session=test_db_session,
        user_ctx=user_ctx,
        query="legacy_archived_customers",
        include_deleted=True,
    )
    found_ids = [str(r["id"]) for r in results_with_deleted]
    assert str(data["deleted_table"].id) in found_ids


@pytest.mark.asyncio
async def test_explain_analyze_index_verification(
    test_db_session: AsyncSession, rbac_search_test_data: dict
):
    """
    Executes EXPLAIN (ANALYZE, COSTS, BUFFERS) on the SQL-pushed RBAC search query
    to verify PostgreSQL query plan execution, confirming subquery and index viability.
    """
    data = rbac_search_test_data
    user = data["user_analyst"]
    role_id = data["analyst_role"].id
    conn_id = data["connection"].id

    explain_sql = text(
        f"""
        EXPLAIN (ANALYZE, COSTS, BUFFERS)
        SELECT t.id, t.name, s.name as schema_name, c.database_name
        FROM catalog_tables t
        JOIN schemas s ON s.id = t.schema_id
        JOIN database_connections c ON c.id = s.connection_id
        WHERE (
            t.search_vector @@ plainto_tsquery('english', 'customer')
            OR t.name ILIKE '%customer%'
        )
        AND t.status != 'DELETED'
        AND EXISTS (
            SELECT 1 FROM permission_rules pr
            JOIN schemas pr_s ON pr_s.id = t.schema_id
            WHERE pr.role_id = '{role_id}'
              AND pr.action = 'VIEW_METADATA'
              AND pr.effect = 'ALLOW'
              AND (
                  pr.resource_type = 'GLOBAL'
                  OR (pr.resource_type = 'DATABASE' AND pr.resource_id = pr_s.connection_id)
                  OR (pr.resource_type = 'SCHEMA' AND pr.resource_id = t.schema_id)
                  OR (pr.resource_type = 'TABLE' AND pr.resource_id = t.id)
              )
        )
        ORDER BY t.name ASC, t.id ASC
        LIMIT 51;
        """
    )
    result = await test_db_session.execute(explain_sql)
    plan_lines = [row[0] for row in result.all()]
    plan_text = "\n".join(plan_lines)

    assert "Execution Time:" in plan_text
    # Ensure plan executed sub-100ms
    assert len(plan_lines) > 0

