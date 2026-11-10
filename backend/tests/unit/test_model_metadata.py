import uuid
import pytest
from sqlalchemy import inspect, Computed
from sqlalchemy.dialects.postgresql import UUID, JSONB, BYTEA, INET, TSVECTOR

from backend.src.models.base import Base
from backend.src.models import (
    Base,
    User,
    Role,
    UserRole,
    UserStatus,
    PermissionRule,
    PermissionAction,
    PermissionEffect,
    ResourceType,
    Category,
    CategoryAssignment,
    EntityType,
    DatabaseConnection,
    DatabaseEngine,
    SyncSchedule,
    DatabaseSchema,
    CatalogTable,
    CatalogColumn,
    TableType,
    LifecycleStatus,
    ClassificationLevel,
    Tag,
    TagAssignment,
    TableVersion,
    SchemaChangeLog,
    SchemaChangeType,
    AuditLog,
)


def test_base_metadata_contains_all_core_tables():
    """Assert all 16 core platform tables are registered in Base.metadata."""
    expected_tables = {
        "users",
        "roles",
        "user_roles",
        "permission_rules",
        "categories",
        "category_assignments",
        "database_connections",
        "sync_schedules",
        "schemas",
        "catalog_tables",
        "catalog_columns",
        "tags",
        "tag_assignments",
        "table_versions",
        "schema_change_logs",
        "audit_logs",
    }
    actual_tables = set(Base.metadata.tables.keys())
    missing_tables = expected_tables - actual_tables
    assert not missing_tables, f"Missing tables in Base.metadata: {missing_tables}"


def test_database_connection_and_sync_schedule_models():
    """Assert DatabaseConnection and SyncSchedule model definitions."""
    conn_table = Base.metadata.tables["database_connections"]
    assert "encrypted_dek" in conn_table.c
    assert "encrypted_credentials" in conn_table.c
    assert "kek_version" in conn_table.c
    assert "custom_params" in conn_table.c

    schedule_table = Base.metadata.tables["sync_schedules"]
    assert "cron_expression" in schedule_table.c
    assert "is_enabled" in schedule_table.c
    assert "last_run_status" in schedule_table.c


def test_schema_and_catalog_table_models():
    """Assert Schema, CatalogTable, and CatalogColumn definitions and generated columns."""
    tables_table = Base.metadata.tables["catalog_tables"]
    assert "effective_description" in tables_table.c
    assert "search_vector" in tables_table.c
    assert "row_count_estimate" in tables_table.c
    assert "size_bytes" in tables_table.c
    assert "custom_properties" in tables_table.c

    # Assert Computed / Generated column definition
    eff_desc_col = tables_table.c["effective_description"]
    assert eff_desc_col.computed is not None or getattr(eff_desc_col, "server_default", None) is not None

    cols_table = Base.metadata.tables["catalog_columns"]
    assert "ordinal_position" in cols_table.c
    assert "raw_data_type" in cols_table.c
    assert "is_primary_key" in cols_table.c
    assert "is_foreign_key" in cols_table.c
    assert "effective_description" in cols_table.c
    assert "search_vector" in cols_table.c


def test_tags_and_tag_assignments_models():
    """Assert Tag and TagAssignment metadata."""
    tag_table = Base.metadata.tables["tags"]
    assert "is_governed" in tag_table.c
    assert "color" in tag_table.c

    assign_table = Base.metadata.tables["tag_assignments"]
    assert "entity_type" in assign_table.c
    assert "entity_id" in assign_table.c
    assert "assigned_by" in assign_table.c


def test_table_version_and_schema_change_log_models():
    """Assert TableVersion and SchemaChangeLog metadata."""
    version_table = Base.metadata.tables["table_versions"]
    assert "version_num" in version_table.c
    assert "snapshot_jsonb" in version_table.c

    change_table = Base.metadata.tables["schema_change_logs"]
    assert "change_type" in change_table.c
    assert "column_name" in change_table.c
    assert "old_value" in change_table.c
    assert "new_value" in change_table.c


def test_audit_log_model():
    """Assert AuditLog model metadata."""
    audit_table = Base.metadata.tables["audit_logs"]
    assert "actor_username" in audit_table.c
    assert "action" in audit_table.c
    assert "entity_type" in audit_table.c
    assert "entity_id" in audit_table.c
    assert "diff_jsonb" in audit_table.c
    assert "ip_address" in audit_table.c
    assert "user_agent" in audit_table.c


def test_orm_relationships_configured():
    """Assert relationship mappers are correctly bound without mapping errors."""
    mapper_conn = inspect(DatabaseConnection)
    assert "schemas" in mapper_conn.relationships
    assert "sync_schedule" in mapper_conn.relationships

    mapper_schema = inspect(DatabaseSchema)
    assert "connection" in mapper_schema.relationships
    assert "tables" in mapper_schema.relationships

    mapper_table = inspect(CatalogTable)
    assert "schema" in mapper_table.relationships
    assert "columns" in mapper_table.relationships
    assert "versions" in mapper_table.relationships
    assert "change_logs" in mapper_table.relationships

    mapper_col = inspect(CatalogColumn)
    assert "table" in mapper_col.relationships

    mapper_tag = inspect(Tag)
    assert "assignments" in mapper_tag.relationships

    mapper_version = inspect(TableVersion)
    assert "table" in mapper_version.relationships
    assert "change_logs" in mapper_version.relationships

    mapper_change = inspect(SchemaChangeLog)
    assert "table" in mapper_change.relationships
    assert "version" in mapper_change.relationships

    mapper_audit = inspect(AuditLog)
    assert "actor" in mapper_audit.relationships


@pytest.mark.asyncio
async def test_models_live_postgres_crud_and_generated_columns(test_db_session):
    """Verify live PostgreSQL interaction, generated columns, and relationship traversal."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    # 1. Connection & Schedule
    conn = DatabaseConnection(
        name=f"test-pg-conn-{uuid.uuid4().hex[:8]}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host="localhost",
        port=5432,
        database_name="analytics_db",
        default_schema="public",
        owner="data_engineering",
        kek_version=1,
        encrypted_dek=b"\x00" * 32,
        encrypted_credentials=b"\x01" * 64,
        custom_params={"sslmode": "require"},
        is_active=True,
    )
    test_db_session.add(conn)
    await test_db_session.flush()

    schedule = SyncSchedule(
        connection_id=conn.id,
        cron_expression="0 */6 * * *",
        is_enabled=True,
    )
    test_db_session.add(schedule)

    # 2. Schema
    schema = DatabaseSchema(
        connection_id=conn.id,
        name="public",
        description="Core public schema",
        owner="analytics_lead",
        status=LifecycleStatus.ACTIVE,
    )
    test_db_session.add(schema)
    await test_db_session.flush()

    # 3. Table & Column
    table = CatalogTable(
        schema_id=schema.id,
        name="customer_orders",
        table_type=TableType.TABLE,
        row_count_estimate=1500000,
        size_bytes=104857600,
        source_description="Raw transaction orders stream",
        owner="ecommerce_team",
        status=LifecycleStatus.ACTIVE,
        custom_properties={"partition_key": "order_date"},
    )
    test_db_session.add(table)
    await test_db_session.flush()

    col1 = CatalogColumn(
        table_id=table.id,
        name="order_id",
        ordinal_position=1,
        data_type="uuid",
        raw_data_type="uuid",
        is_nullable=False,
        is_primary_key=True,
        classification_level=ClassificationLevel.INTERNAL,
        status=LifecycleStatus.ACTIVE,
    )
    col2 = CatalogColumn(
        table_id=table.id,
        name="customer_ssn",
        ordinal_position=2,
        data_type="varchar(11)",
        raw_data_type="varchar(11)",
        is_nullable=True,
        is_primary_key=False,
        source_description="Sensitive SSN number",
        classification_level=ClassificationLevel.PII,
        status=LifecycleStatus.ACTIVE,
    )
    test_db_session.add_all([col1, col2])

    # 4. Tags
    tag = Tag(
        name=f"pii-restricted-{uuid.uuid4().hex[:6]}",
        color="#D32F2F",
        description="High sensitivity data requiring strict RBAC",
        is_governed=True,
    )
    test_db_session.add(tag)
    await test_db_session.flush()

    tag_assign = TagAssignment(
        tag_id=tag.id,
        entity_type=EntityType.COLUMN,
        entity_id=col2.id,
    )
    test_db_session.add(tag_assign)

    # 5. Versioning
    version = TableVersion(
        table_id=table.id,
        version_num=1,
        snapshot_jsonb={"columns": ["order_id", "customer_ssn"]},
    )
    test_db_session.add(version)
    await test_db_session.flush()

    change_log = SchemaChangeLog(
        table_id=table.id,
        version_id=version.id,
        change_type=SchemaChangeType.COLUMN_ADDED,
        column_name="customer_ssn",
        new_value={"data_type": "varchar(11)", "is_nullable": True},
    )
    test_db_session.add(change_log)

    # 6. Audit Log
    audit = AuditLog(
        actor_username="system_admin",
        action="CREATE_TABLE_METADATA",
        entity_type=EntityType.TABLE,
        entity_id=table.id,
        diff_jsonb={"action": "initial_sync"},
    )
    test_db_session.add(audit)
    await test_db_session.flush()

    # 7. Verification: Re-query with eager relationships
    stmt = (
        select(CatalogTable)
        .where(CatalogTable.id == table.id)
        .options(
            selectinload(CatalogTable.schema).selectinload(DatabaseSchema.connection),
            selectinload(CatalogTable.columns),
            selectinload(CatalogTable.versions),
            selectinload(CatalogTable.change_logs),
        )
    )
    result = await test_db_session.execute(stmt)
    loaded_table = result.scalar_one()

    assert loaded_table.name == "customer_orders"
    assert loaded_table.schema.connection.name == conn.name
    assert len(loaded_table.columns) == 2
    assert loaded_table.effective_description == "Raw transaction orders stream"
    assert loaded_table.search_vector is not None
    assert len(loaded_table.versions) == 1
    assert len(loaded_table.change_logs) == 1

