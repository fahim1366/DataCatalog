from __future__ import annotations

import uuid
import pytest
from sqlalchemy import select
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
from backend.src.models.identity import User, UserStatus
from backend.src.models.versioning import SchemaChangeLog, SchemaChangeType, TableVersion
from backend.src.services.auth.jwt_service import auth_service
from backend.src.services.catalog.version_service import version_service


@pytest.fixture
async def version_test_data(test_db_session: AsyncSession):
    """Sets up a database connection, schema, table, and user for versioning tests."""
    suffix = uuid.uuid4().hex[:8]

    # Create User
    user = User(
        id=uuid.uuid4(),
        username=f"steward_{suffix}",
        email=f"steward_{suffix}@company.internal",
        hashed_password=auth_service.hash_password("Password123!"),
        full_name="Data Steward",
        status=UserStatus.ACTIVE,
        is_superadmin=True,
    )
    test_db_session.add(user)

    # Create Database Connection
    connection = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"pg_conn_{suffix}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host="localhost",
        port=5432,
        database_name=f"db_{suffix}",
        owner=user.username,
        kek_version=1,
        encrypted_dek=b"dummy_encrypted_dek_32_bytes_data",
        encrypted_credentials=b"dummy_encrypted_credentials_data",
    )
    test_db_session.add(connection)
    await test_db_session.flush()

    # Create Schema
    schema = DatabaseSchema(
        id=uuid.uuid4(),
        connection_id=connection.id,
        name=f"public_{suffix}",
        owner=user.username,
    )
    test_db_session.add(schema)
    await test_db_session.flush()

    # Create Table
    table = CatalogTable(
        id=uuid.uuid4(),
        schema_id=schema.id,
        name=f"customer_metrics_{suffix}",
        table_type=TableType.TABLE,
        row_count_estimate=5000,
        size_bytes=1048576,
        source_description="Customer analytical metrics",
        approved_description="Curated metrics table",
        owner=user.username,
        status=LifecycleStatus.ACTIVE,
    )
    test_db_session.add(table)
    await test_db_session.flush()

    # Add initial columns
    col_id = CatalogColumn(
        id=uuid.uuid4(),
        table_id=table.id,
        name="customer_id",
        ordinal_position=1,
        data_type="UUID",
        raw_data_type="uuid",
        is_nullable=False,
        is_primary_key=True,
        is_foreign_key=False,
        classification_level=ClassificationLevel.INTERNAL,
        status=LifecycleStatus.ACTIVE,
    )
    col_score = CatalogColumn(
        id=uuid.uuid4(),
        table_id=table.id,
        name="churn_score",
        ordinal_position=2,
        data_type="NUMERIC(5,2)",
        raw_data_type="numeric(5,2)",
        is_nullable=True,
        is_primary_key=False,
        is_foreign_key=False,
        classification_level=ClassificationLevel.INTERNAL,
        status=LifecycleStatus.ACTIVE,
    )
    test_db_session.add_all([col_id, col_score])
    await test_db_session.flush()

    return {
        "user": user,
        "connection": connection,
        "schema": schema,
        "table": table,
        "col_id": col_id,
        "col_score": col_score,
        "suffix": suffix,
    }


@pytest.mark.asyncio
async def test_initial_snapshot_creation(
    test_db_session: AsyncSession, version_test_data: dict
):
    """
    T029: Verifies snapshot creation for a table at version 1.
    Asserts snapshot contains table metadata and column definitions.
    """
    table = version_test_data["table"]
    user = version_test_data["user"]

    version = await version_service.capture_version(
        session=test_db_session,
        table_id=table.id,
        actor_id=user.id,
        actor_username=user.username,
    )

    assert version is not None
    assert version.table_id == table.id
    assert version.version_num == 1
    assert version.created_by == user.id

    snapshot = version.snapshot_jsonb
    assert snapshot["name"] == table.name
    assert snapshot["table_type"] == "TABLE"
    assert "columns" in snapshot
    assert len(snapshot["columns"]) == 2

    col_names = [col["name"] for col in snapshot["columns"]]
    assert "customer_id" in col_names
    assert "churn_score" in col_names


@pytest.mark.asyncio
async def test_version_increments_and_delta_calculation(
    test_db_session: AsyncSession, version_test_data: dict
):
    """
    T029: Verifies that:
    1. Initial snapshot captures version 1.
    2. Column mutations (new column, removed column, changed type) increment version to 2.
    3. Column deltas correctly identify COLUMN_ADDED, COLUMN_REMOVED, and TYPE_CHANGED.
    4. SchemaChangeLog entries are persisted with corresponding change types.
    """
    table = version_test_data["table"]
    user = version_test_data["user"]
    col_score = version_test_data["col_score"]

    # 1. Capture initial version 1
    v1 = await version_service.capture_version(
        session=test_db_session,
        table_id=table.id,
        actor_id=user.id,
        actor_username=user.username,
    )
    assert v1.version_num == 1

    # 2. Modify schema:
    # - Soft-delete col_score (simulate COLUMN_REMOVED / COLUMN_DROPPED)
    col_score.status = LifecycleStatus.DELETED

    # - Modify type of customer_id from UUID to VARCHAR(64) (simulate TYPE_CHANGED)
    version_test_data["col_id"].data_type = "VARCHAR(64)"
    version_test_data["col_id"].raw_data_type = "varchar(64)"

    # - Add new column lifetime_value (simulate COLUMN_ADDED)
    col_ltv = CatalogColumn(
        id=uuid.uuid4(),
        table_id=table.id,
        name="lifetime_value",
        ordinal_position=3,
        data_type="DECIMAL(10,2)",
        raw_data_type="decimal(10,2)",
        is_nullable=True,
        is_primary_key=False,
        is_foreign_key=False,
        classification_level=ClassificationLevel.INTERNAL,
        status=LifecycleStatus.ACTIVE,
    )
    test_db_session.add(col_ltv)
    await test_db_session.flush()

    # 3. Capture next version
    v2 = await version_service.capture_version_if_changed(
        session=test_db_session,
        table_id=table.id,
        actor_id=user.id,
        actor_username=user.username,
    )

    assert v2 is not None
    assert v2.version_num == 2

    # 4. Assert column delta calculations
    deltas = version_service.compute_column_deltas(
        v1.snapshot_jsonb, v2.snapshot_jsonb
    )
    change_types = {d["change_type"] for d in deltas}

    assert "COLUMN_ADDED" in change_types
    assert "COLUMN_REMOVED" in change_types
    assert "TYPE_CHANGED" in change_types

    # Check individual delta payloads
    added_delta = next(d for d in deltas if d["change_type"] == "COLUMN_ADDED")
    assert added_delta["column_name"] == "lifetime_value"
    assert added_delta["new_value"]["data_type"] == "DECIMAL(10,2)"

    removed_delta = next(d for d in deltas if d["change_type"] == "COLUMN_REMOVED")
    assert removed_delta["column_name"] == "churn_score"

    type_delta = next(d for d in deltas if d["change_type"] == "TYPE_CHANGED")
    assert type_delta["column_name"] == "customer_id"
    assert type_delta["old_value"]["data_type"] == "UUID"
    assert type_delta["new_value"]["data_type"] == "VARCHAR(64)"

    # 5. Assert SchemaChangeLog records in database
    stmt = select(SchemaChangeLog).where(SchemaChangeLog.version_id == v2.id)
    result = await test_db_session.execute(stmt)
    change_logs = list(result.scalars().all())

    db_change_types = {log.change_type for log in change_logs}
    assert SchemaChangeType.COLUMN_ADDED in db_change_types
    assert SchemaChangeType.COLUMN_DROPPED in db_change_types
    assert SchemaChangeType.TYPE_CHANGED in db_change_types


@pytest.mark.asyncio
async def test_version_not_incremented_when_no_structural_changes(
    test_db_session: AsyncSession, version_test_data: dict
):
    """
    T029: Verifies that calling capture_version_if_changed when no column/type
    modifications have occurred returns None and does NOT increment version.
    """
    table = version_test_data["table"]
    user = version_test_data["user"]

    v1 = await version_service.capture_version(
        session=test_db_session,
        table_id=table.id,
        actor_id=user.id,
        actor_username=user.username,
    )
    assert v1.version_num == 1

    # Call again without changing schema
    v_next = await version_service.capture_version_if_changed(
        session=test_db_session,
        table_id=table.id,
        actor_id=user.id,
        actor_username=user.username,
    )
    assert v_next is None

    # Verify only version 1 exists
    stmt = select(TableVersion).where(TableVersion.table_id == table.id)
    res = await test_db_session.execute(stmt)
    versions = list(res.scalars().all())
    assert len(versions) == 1
    assert versions[0].version_num == 1

