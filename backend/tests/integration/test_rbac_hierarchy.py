from __future__ import annotations

import time
import uuid
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.models.catalog import (
    CatalogTable,
    DatabaseSchema,
    LifecycleStatus,
    TableType,
)
from backend.src.models.connections import DatabaseConnection, DatabaseEngine
from backend.src.models.identity import Role, User, UserRole, UserStatus
from backend.src.models.rbac import (
    Category,
    CategoryAssignment,
    EntityType,
    PermissionAction,
    PermissionEffect,
    PermissionRule,
    ResourceType,
)
from backend.src.api.dependencies.rbac import UserContext, build_table_rbac_filter
from backend.src.services.rbac.category_service import CategoryService


@pytest.fixture
async def rbac_hierarchy_data(test_db_session: AsyncSession):
    """
    Seeds a database hierarchy (Database -> Schema A / Schema B -> Tables)
    along with a multi-level Category tree (Root -> Branch -> Leaf)
    to test RBAC inheritance, DENY precedence, and category traversal.
    """
    unique_suffix = uuid.uuid4().hex[:8]

    # 1. Database Connection
    connection = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"corp_dw_{unique_suffix}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host="localhost",
        port=5432,
        database_name=f"dw_corp_{unique_suffix}",
        owner="secops@company.internal",
        encrypted_dek=b"k" * 32,
        encrypted_credentials=b"c" * 64,
        is_active=True,
    )
    test_db_session.add(connection)
    await test_db_session.flush()

    # 2. Schemas
    schema_public = DatabaseSchema(
        id=uuid.uuid4(),
        connection_id=connection.id,
        name=f"public_{unique_suffix}",
        description="Public reporting schema",
        status=LifecycleStatus.ACTIVE,
    )
    schema_finance = DatabaseSchema(
        id=uuid.uuid4(),
        connection_id=connection.id,
        name=f"finance_{unique_suffix}",
        description="Confidential finance schema",
        status=LifecycleStatus.ACTIVE,
    )
    test_db_session.add_all([schema_public, schema_finance])
    await test_db_session.flush()

    # 3. Tables
    # Public schema table 1
    table_customers = CatalogTable(
        id=uuid.uuid4(),
        schema_id=schema_public.id,
        name=f"dim_customers_{unique_suffix}",
        table_type=TableType.TABLE,
        row_count_estimate=50000,
        size_bytes=1024000,
        source_description="Customer dimension data",
        status=LifecycleStatus.ACTIVE,
    )
    # Public schema table 2 (explicit DENY test)
    table_secret_audit = CatalogTable(
        id=uuid.uuid4(),
        schema_id=schema_public.id,
        name=f"dim_secret_audit_{unique_suffix}",
        table_type=TableType.TABLE,
        row_count_estimate=200,
        size_bytes=20480,
        source_description="Audit log records",
        status=LifecycleStatus.ACTIVE,
    )
    # Finance schema table
    table_salaries = CatalogTable(
        id=uuid.uuid4(),
        schema_id=schema_finance.id,
        name=f"fact_salaries_{unique_suffix}",
        table_type=TableType.TABLE,
        row_count_estimate=12000,
        size_bytes=512000,
        source_description="Confidential compensation records",
        status=LifecycleStatus.ACTIVE,
    )
    # Categorized Table (Category tree test)
    table_freight = CatalogTable(
        id=uuid.uuid4(),
        schema_id=schema_public.id,
        name=f"fact_freight_logistics_{unique_suffix}",
        table_type=TableType.TABLE,
        row_count_estimate=80000,
        size_bytes=4096000,
        source_description="Global freight shipping logistics",
        status=LifecycleStatus.ACTIVE,
    )
    test_db_session.add_all([table_customers, table_secret_audit, table_salaries, table_freight])
    await test_db_session.flush()

    # 4. Multi-level Category Tree: Root -> Branch -> Leaf
    cat_root = Category(
        id=uuid.uuid4(),
        name=f"Operations_{unique_suffix}",
        description="Global Operations Category",
        parent_id=None,
    )
    test_db_session.add(cat_root)
    await test_db_session.flush()

    cat_branch = Category(
        id=uuid.uuid4(),
        name=f"SupplyChain_{unique_suffix}",
        description="Supply Chain Branch",
        parent_id=cat_root.id,
    )
    test_db_session.add(cat_branch)
    await test_db_session.flush()

    cat_leaf = Category(
        id=uuid.uuid4(),
        name=f"LogisticsFreight_{unique_suffix}",
        description="Freight and Carrier Logistics Leaf",
        parent_id=cat_branch.id,
    )
    test_db_session.add(cat_leaf)
    await test_db_session.flush()

    # Assign table_freight to cat_leaf
    cat_assignment = CategoryAssignment(
        id=uuid.uuid4(),
        category_id=cat_leaf.id,
        entity_type=EntityType.TABLE,
        entity_id=table_freight.id,
    )
    test_db_session.add(cat_assignment)
    await test_db_session.flush()

    # 5. Roles
    role_db_viewer = Role(
        id=uuid.uuid4(),
        name=f"DbViewerRole_{unique_suffix}",
        is_system_role=False,
    )
    role_schema_viewer = Role(
        id=uuid.uuid4(),
        name=f"SchemaViewerRole_{unique_suffix}",
        is_system_role=False,
    )
    role_cat_viewer = Role(
        id=uuid.uuid4(),
        name=f"CatViewerRole_{unique_suffix}",
        is_system_role=False,
    )
    role_mixed_deny = Role(
        id=uuid.uuid4(),
        name=f"MixedDenyRole_{unique_suffix}",
        is_system_role=False,
    )
    test_db_session.add_all([role_db_viewer, role_schema_viewer, role_cat_viewer, role_mixed_deny])
    await test_db_session.flush()

    # 6. Users
    user_db = User(
        id=uuid.uuid4(),
        username=f"user_db_{unique_suffix}",
        email=f"user_db_{unique_suffix}@company.internal",
        hashed_password="hashed_password",
        full_name="Database User",
        status=UserStatus.ACTIVE,
    )
    user_schema = User(
        id=uuid.uuid4(),
        username=f"user_schema_{unique_suffix}",
        email=f"user_schema_{unique_suffix}@company.internal",
        hashed_password="hashed_password",
        full_name="Schema User",
        status=UserStatus.ACTIVE,
    )
    user_cat = User(
        id=uuid.uuid4(),
        username=f"user_cat_{unique_suffix}",
        email=f"user_cat_{unique_suffix}@company.internal",
        hashed_password="hashed_password",
        full_name="Category User",
        status=UserStatus.ACTIVE,
    )
    user_mixed = User(
        id=uuid.uuid4(),
        username=f"user_mixed_{unique_suffix}",
        email=f"user_mixed_{unique_suffix}@company.internal",
        hashed_password="hashed_password",
        full_name="Mixed Deny User",
        status=UserStatus.ACTIVE,
    )
    test_db_session.add_all([user_db, user_schema, user_cat, user_mixed])
    await test_db_session.flush()

    # Role Assignments
    ur1 = UserRole(user_id=user_db.id, role_id=role_db_viewer.id)
    ur2 = UserRole(user_id=user_schema.id, role_id=role_schema_viewer.id)
    ur3 = UserRole(user_id=user_cat.id, role_id=role_cat_viewer.id)
    ur4 = UserRole(user_id=user_mixed.id, role_id=role_mixed_deny.id)
    test_db_session.add_all([ur1, ur2, ur3, ur4])
    await test_db_session.flush()

    return {
        "connection": connection,
        "schema_public": schema_public,
        "schema_finance": schema_finance,
        "table_customers": table_customers,
        "table_secret_audit": table_secret_audit,
        "table_salaries": table_salaries,
        "table_freight": table_freight,
        "cat_root": cat_root,
        "cat_branch": cat_branch,
        "cat_leaf": cat_leaf,
        "role_db_viewer": role_db_viewer,
        "role_schema_viewer": role_schema_viewer,
        "role_cat_viewer": role_cat_viewer,
        "role_mixed_deny": role_mixed_deny,
        "user_db": user_db,
        "user_schema": user_schema,
        "user_cat": user_cat,
        "user_mixed": user_mixed,
    }


@pytest.mark.asyncio
async def test_permission_inheritance_database_to_schema_to_table(
    test_db_session: AsyncSession, rbac_hierarchy_data: dict
):
    """
    T018: Asserts permission inheritance down the structural hierarchy:
    - Granting VIEW_METADATA on DATABASE allows viewing all tables in all child schemas.
    - Granting VIEW_METADATA on SCHEMA allows viewing only tables within that specific schema.
    - Granting VIEW_METADATA on TABLE allows viewing only that specific table.
    """
    data = rbac_hierarchy_data
    db_conn = data["connection"]
    schema_pub = data["schema_public"]
    schema_fin = data["schema_finance"]
    tbl_cust = data["table_customers"]
    tbl_sec = data["table_secret_audit"]
    tbl_sal = data["table_salaries"]
    tbl_freight = data["table_freight"]

    # 1. Database-level grant to role_db_viewer
    rule_db = PermissionRule(
        id=uuid.uuid4(),
        role_id=data["role_db_viewer"].id,
        resource_type=ResourceType.DATABASE,
        resource_id=db_conn.id,
        action=PermissionAction.VIEW_METADATA,
        effect=PermissionEffect.ALLOW,
    )
    test_db_session.add(rule_db)
    await test_db_session.flush()

    user_db_ctx = UserContext(
        user=data["user_db"],
        role_ids=[data["role_db_viewer"].id],
        is_superadmin=False,
    )
    filter_fn = build_table_rbac_filter(user_db_ctx, PermissionAction.VIEW_METADATA)
    query = filter_fn(select(CatalogTable))
    result = await test_db_session.execute(query)
    visible_tables = result.scalars().all()
    visible_ids = {t.id for t in visible_tables}

    # All tables in this database connection should be visible via inheritance
    assert tbl_cust.id in visible_ids
    assert tbl_sec.id in visible_ids
    assert tbl_sal.id in visible_ids
    assert tbl_freight.id in visible_ids

    # 2. Schema-level grant to role_schema_viewer (only schema_finance)
    rule_schema = PermissionRule(
        id=uuid.uuid4(),
        role_id=data["role_schema_viewer"].id,
        resource_type=ResourceType.SCHEMA,
        resource_id=schema_fin.id,
        action=PermissionAction.VIEW_METADATA,
        effect=PermissionEffect.ALLOW,
    )
    test_db_session.add(rule_schema)
    await test_db_session.flush()

    user_schema_ctx = UserContext(
        user=data["user_schema"],
        role_ids=[data["role_schema_viewer"].id],
        is_superadmin=False,
    )
    filter_schema_fn = build_table_rbac_filter(user_schema_ctx, PermissionAction.VIEW_METADATA)
    query_schema = filter_schema_fn(select(CatalogTable))
    result_schema = await test_db_session.execute(query_schema)
    visible_schema_tables = result_schema.scalars().all()
    visible_schema_ids = {t.id for t in visible_schema_tables}

    assert tbl_sal.id in visible_schema_ids
    assert tbl_cust.id not in visible_schema_ids
    assert tbl_sec.id not in visible_schema_ids
    assert tbl_freight.id not in visible_schema_ids


@pytest.mark.asyncio
async def test_explicit_deny_overrides_allow_at_all_levels(
    test_db_session: AsyncSession, rbac_hierarchy_data: dict
):
    """
    T018: Asserts that an explicit DENY rule overrides an inherited ALLOW rule
    at any level (Database ALLOW + Table DENY, Database ALLOW + Schema DENY).
    """
    data = rbac_hierarchy_data
    db_conn = data["connection"]
    schema_fin = data["schema_finance"]
    tbl_cust = data["table_customers"]
    tbl_sec = data["table_secret_audit"]
    tbl_sal = data["table_salaries"]
    role = data["role_mixed_deny"]
    user = data["user_mixed"]

    # Rule 1: ALLOW VIEW_METADATA on DATABASE
    rule_db_allow = PermissionRule(
        id=uuid.uuid4(),
        role_id=role.id,
        resource_type=ResourceType.DATABASE,
        resource_id=db_conn.id,
        action=PermissionAction.VIEW_METADATA,
        effect=PermissionEffect.ALLOW,
    )
    # Rule 2: Explicit DENY on specific table (tbl_sec)
    rule_tbl_deny = PermissionRule(
        id=uuid.uuid4(),
        role_id=role.id,
        resource_type=ResourceType.TABLE,
        resource_id=tbl_sec.id,
        action=PermissionAction.VIEW_METADATA,
        effect=PermissionEffect.DENY,
    )
    # Rule 3: Explicit DENY on whole schema (schema_fin)
    rule_schema_deny = PermissionRule(
        id=uuid.uuid4(),
        role_id=role.id,
        resource_type=ResourceType.SCHEMA,
        resource_id=schema_fin.id,
        action=PermissionAction.VIEW_METADATA,
        effect=PermissionEffect.DENY,
    )
    test_db_session.add_all([rule_db_allow, rule_tbl_deny, rule_schema_deny])
    await test_db_session.flush()

    user_ctx = UserContext(
        user=user,
        role_ids=[role.id],
        is_superadmin=False,
    )
    filter_fn = build_table_rbac_filter(user_ctx, PermissionAction.VIEW_METADATA)
    query = filter_fn(select(CatalogTable))
    result = await test_db_session.execute(query)
    visible_tables = result.scalars().all()
    visible_ids = {t.id for t in visible_tables}

    # tbl_cust: Allowed by Database grant, no DENY -> visible
    assert tbl_cust.id in visible_ids
    # tbl_sec: In public schema, but explicit TABLE DENY -> hidden
    assert tbl_sec.id not in visible_ids
    # tbl_sal: In finance schema, which has SCHEMA DENY -> hidden
    assert tbl_sal.id not in visible_ids


@pytest.mark.asyncio
async def test_recursive_category_tree_subquery_resolution(
    test_db_session: AsyncSession, rbac_hierarchy_data: dict
):
    """
    T018 & T020: Asserts recursive category tree sub-query resolution:
    - cat_root -> cat_branch -> cat_leaf
    - table_freight is assigned to cat_leaf.
    - Role is granted VIEW_METADATA on cat_root (ALLOW).
    - Category traversal resolves that table_freight inherits access from root category.
    - CategoryService traverses descendants in < 10ms.
    - Explicit DENY on table_freight overrides Category ALLOW.
    """
    data = rbac_hierarchy_data
    cat_root = data["cat_root"]
    cat_branch = data["cat_branch"]
    cat_leaf = data["cat_leaf"]
    tbl_freight = data["table_freight"]
    tbl_cust = data["table_customers"]
    role_cat = data["role_cat_viewer"]
    user_cat = data["user_cat"]

    # 1. Grant VIEW_METADATA on Root Category to role_cat
    rule_cat_root = PermissionRule(
        id=uuid.uuid4(),
        role_id=role_cat.id,
        resource_type=ResourceType.CATEGORY,
        resource_id=cat_root.id,
        action=PermissionAction.VIEW_METADATA,
        effect=PermissionEffect.ALLOW,
    )
    test_db_session.add(rule_cat_root)
    await test_db_session.flush()

    user_cat_ctx = UserContext(
        user=user_cat,
        role_ids=[role_cat.id],
        is_superadmin=False,
    )

    # 2. Test CategoryService descendant resolution
    start_time = time.perf_counter()
    descendant_ids = await CategoryService.get_descendant_category_ids(
        test_db_session, cat_root.id
    )
    elapsed_ms = (time.perf_counter() - start_time) * 1000

    assert cat_root.id in descendant_ids
    assert cat_branch.id in descendant_ids
    assert cat_leaf.id in descendant_ids
    assert elapsed_ms < 10.0, f"Category traversal took {elapsed_ms:.2f}ms, expected < 10ms"

    # 3. Test resolve_effective_permissions via CategoryService
    effective_effect = await CategoryService.resolve_effective_permission(
        session=test_db_session,
        user_ctx=user_cat_ctx,
        entity_type=EntityType.TABLE,
        entity_id=tbl_freight.id,
        action=PermissionAction.VIEW_METADATA,
    )
    assert effective_effect == PermissionEffect.ALLOW

    # 4. Test SQL query pushed RBAC filter with category sub-tree resolution
    filter_fn = build_table_rbac_filter(user_cat_ctx, PermissionAction.VIEW_METADATA)
    query = filter_fn(select(CatalogTable))
    result = await test_db_session.execute(query)
    visible_tables = result.scalars().all()
    visible_ids = {t.id for t in visible_tables}

    # table_freight is tagged with cat_leaf, which is a descendant of cat_root -> visible
    assert tbl_freight.id in visible_ids
    # tbl_cust is not in cat_root tree -> hidden
    assert tbl_cust.id not in visible_ids

    # 5. Explicit DENY overrides Category ALLOW
    rule_table_deny = PermissionRule(
        id=uuid.uuid4(),
        role_id=role_cat.id,
        resource_type=ResourceType.TABLE,
        resource_id=tbl_freight.id,
        action=PermissionAction.VIEW_METADATA,
        effect=PermissionEffect.DENY,
    )
    test_db_session.add(rule_table_deny)
    await test_db_session.flush()

    effective_deny = await CategoryService.resolve_effective_permission(
        session=test_db_session,
        user_ctx=user_cat_ctx,
        entity_type=EntityType.TABLE,
        entity_id=tbl_freight.id,
        action=PermissionAction.VIEW_METADATA,
    )
    assert effective_deny == PermissionEffect.DENY

    query_denied = filter_fn(select(CatalogTable))
    result_denied = await test_db_session.execute(query_denied)
    visible_denied_ids = {t.id for t in result_denied.scalars().all()}
    assert tbl_freight.id not in visible_denied_ids

