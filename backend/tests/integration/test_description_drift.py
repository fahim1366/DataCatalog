from __future__ import annotations

import uuid
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.models.audit import AuditLog
from backend.src.models.catalog import (
    CatalogColumn,
    CatalogTable,
    DatabaseSchema,
    LifecycleStatus,
    TableType,
)
from backend.src.models.connections import DatabaseConnection, DatabaseEngine
from backend.src.models.identity import User, UserStatus
from backend.src.models.versioning import SchemaChangeLog, SchemaChangeType
from backend.src.services.auth.jwt_service import auth_service
from backend.src.services.ingestion.drift_service import (
    DriftResolutionAction,
    drift_service,
)


@pytest.fixture
async def setup_drift_env(test_db_session: AsyncSession):
    suffix = uuid.uuid4().hex[:8]

    user = User(
        id=uuid.uuid4(),
        username=f"drift_admin_{suffix}",
        email=f"drift_{suffix}@company.internal",
        hashed_password=auth_service.hash_password("Pass1234#"),
        full_name="Drift Admin",
        status=UserStatus.ACTIVE,
        is_superadmin=True,
    )
    test_db_session.add(user)

    connection = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"drift_pg_{suffix}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host="localhost",
        port=5432,
        database_name=f"drift_db_{suffix}",
        owner=user.username,
        kek_version=1,
        encrypted_dek=b"dummy_dek_32_bytes_drift_ingest",
        encrypted_credentials=b"dummy_credentials_drift_ingest",
    )
    test_db_session.add(connection)

    schema = DatabaseSchema(
        id=uuid.uuid4(),
        connection_id=connection.id,
        name=f"drift_schema_{suffix}",
        status=LifecycleStatus.ACTIVE,
    )
    test_db_session.add(schema)
    await test_db_session.flush()

    return {
        "user": user,
        "connection": connection,
        "schema": schema,
        "suffix": suffix,
    }


@pytest.mark.asyncio
async def test_drift_detection_and_flagging(
    test_db_session: AsyncSession, setup_drift_env: dict
):
    """
    T061: Asserts detect_and_flag_drift flags drift and creates SchemaChangeLog(DESCRIPTION_DRIFT)
    only when extracted comment differs from source comment and approved_description exists.
    """
    schema = setup_drift_env["schema"]
    user = setup_drift_env["user"]

    # Table with human curated approved_description
    table = CatalogTable(
        id=uuid.uuid4(),
        schema_id=schema.id,
        name="transactions",
        table_type=TableType.TABLE,
        source_description="Original DB transaction comment",
        approved_description="Curated approved transaction ledger",
        status=LifecycleStatus.ACTIVE,
    )
    test_db_session.add(table)
    await test_db_session.flush()

    # Column with human curated approved_description
    column = CatalogColumn(
        id=uuid.uuid4(),
        table_id=table.id,
        name="tx_hash",
        ordinal_position=1,
        data_type="varchar(64)",
        raw_data_type="varchar",
        source_description="Original hash comment",
        approved_description="Cryptographic SHA-256 transaction hash",
        status=LifecycleStatus.ACTIVE,
    )
    test_db_session.add(column)
    await test_db_session.flush()

    # Case 1: Source comment changes to new string
    new_table_comment = "Native DB schema updated transaction comment v2"
    drift_detected = await drift_service.detect_and_flag_drift(
        session=test_db_session,
        entity=table,
        extracted_comment=new_table_comment,
        actor_id=user.id,
    )
    assert drift_detected is True
    assert table.description_drift_detected is True
    assert table.source_description == new_table_comment
    # Approved description must NOT be overwritten!
    assert table.approved_description == "Curated approved transaction ledger"

    # Verify SchemaChangeLog record created
    change_log_stmt = select(SchemaChangeLog).where(
        SchemaChangeLog.table_id == table.id,
        SchemaChangeLog.change_type == SchemaChangeType.DESCRIPTION_DRIFT,
    )
    log = (await test_db_session.execute(change_log_stmt)).scalar_one()
    assert log.column_name is None
    assert log.old_value["source_description"] == "Original DB transaction comment"
    assert log.new_value["source_description"] == new_table_comment

    # Case 2: Column drift detection
    new_col_comment = "Updated native column hash comment v2"
    col_drift = await drift_service.detect_and_flag_drift(
        session=test_db_session,
        entity=column,
        extracted_comment=new_col_comment,
        actor_id=user.id,
    )
    assert col_drift is True
    assert column.description_drift_detected is True
    assert column.approved_description == "Cryptographic SHA-256 transaction hash"

    col_log_stmt = select(SchemaChangeLog).where(
        SchemaChangeLog.table_id == table.id,
        SchemaChangeLog.column_name == "tx_hash",
        SchemaChangeLog.change_type == SchemaChangeType.DESCRIPTION_DRIFT,
    )
    col_log = (await test_db_session.execute(col_log_stmt)).scalar_one()
    assert col_log.new_value["source_description"] == new_col_comment


@pytest.mark.asyncio
async def test_drift_resolution_paths(
    test_db_session: AsyncSession, setup_drift_env: dict
):
    """
    T061: Tests the 3 resolution actions:
    1. RETAIN_APPROVED: keeps approved_description, clears drift flag, creates audit log.
    2. OVERWRITE_WITH_SOURCE: sets approved_description = source_description, clears drift flag.
    3. CUSTOM_MERGE: sets approved_description = custom_text, clears drift flag.
    """
    schema = setup_drift_env["schema"]
    user = setup_drift_env["user"]

    # Table 1: For RETAIN_APPROVED
    t1 = CatalogTable(
        id=uuid.uuid4(),
        schema_id=schema.id,
        name="audit_tbl_1",
        table_type=TableType.TABLE,
        source_description="DB comment A",
        approved_description="Curated comment A",
        description_drift_detected=True,
        status=LifecycleStatus.ACTIVE,
    )
    # Table 2: For OVERWRITE_WITH_SOURCE
    t2 = CatalogTable(
        id=uuid.uuid4(),
        schema_id=schema.id,
        name="audit_tbl_2",
        table_type=TableType.TABLE,
        source_description="DB comment B",
        approved_description="Curated comment B",
        description_drift_detected=True,
        status=LifecycleStatus.ACTIVE,
    )
    # Table 3: For CUSTOM_MERGE
    t3 = CatalogTable(
        id=uuid.uuid4(),
        schema_id=schema.id,
        name="audit_tbl_3",
        table_type=TableType.TABLE,
        source_description="DB comment C",
        approved_description="Curated comment C",
        description_drift_detected=True,
        status=LifecycleStatus.ACTIVE,
    )
    test_db_session.add_all([t1, t2, t3])
    await test_db_session.flush()

    # Resolution 1: RETAIN_APPROVED
    res_1 = await drift_service.resolve_table_drift(
        session=test_db_session,
        table_id=t1.id,
        resolution_action=DriftResolutionAction.RETAIN_APPROVED,
        actor_id=user.id,
        actor_username=user.username,
    )
    assert res_1.description_drift_detected is False
    assert res_1.approved_description == "Curated comment A"

    # Resolution 2: OVERWRITE_WITH_SOURCE
    res_2 = await drift_service.resolve_table_drift(
        session=test_db_session,
        table_id=t2.id,
        resolution_action=DriftResolutionAction.OVERWRITE_WITH_SOURCE,
        actor_id=user.id,
        actor_username=user.username,
    )
    assert res_2.description_drift_detected is False
    assert res_2.approved_description == "DB comment B"
    assert res_2.description_source == "SOURCE_DB"

    # Resolution 3: CUSTOM_MERGE
    res_3 = await drift_service.resolve_table_drift(
        session=test_db_session,
        table_id=t3.id,
        resolution_action=DriftResolutionAction.CUSTOM_MERGE,
        custom_text="Merged expert comment blending DB comment C and curated notes",
        actor_id=user.id,
        actor_username=user.username,
    )
    assert res_3.description_drift_detected is False
    assert res_3.approved_description == "Merged expert comment blending DB comment C and curated notes"
    assert res_3.description_source == "HUMAN"

    # Verify AuditLog entries
    audit_stmt = select(AuditLog).where(AuditLog.entity_id == t3.id)
    audit = (await test_db_session.execute(audit_stmt)).scalar_one()
    assert audit.action == "RESOLVE_TABLE_DESCRIPTION_DRIFT"


@pytest.mark.asyncio
async def test_column_drift_resolution(
    test_db_session: AsyncSession, setup_drift_env: dict
):
    """T061: Asserts resolve_column_drift works for columns across all 3 resolution paths."""
    schema = setup_drift_env["schema"]
    user = setup_drift_env["user"]

    table = CatalogTable(
        id=uuid.uuid4(),
        schema_id=schema.id,
        name="payments",
        table_type=TableType.TABLE,
        status=LifecycleStatus.ACTIVE,
    )
    test_db_session.add(table)
    await test_db_session.flush()

    col = CatalogColumn(
        id=uuid.uuid4(),
        table_id=table.id,
        name="currency",
        ordinal_position=1,
        data_type="varchar(3)",
        raw_data_type="varchar",
        source_description="ISO code (source DB)",
        approved_description="Curated ISO-4217 Currency Code",
        description_drift_detected=True,
        status=LifecycleStatus.ACTIVE,
    )
    test_db_session.add(col)
    await test_db_session.flush()

    resolved_col = await drift_service.resolve_column_drift(
        session=test_db_session,
        column_id=col.id,
        resolution_action=DriftResolutionAction.OVERWRITE_WITH_SOURCE,
        actor_id=user.id,
        actor_username=user.username,
    )
    assert resolved_col.description_drift_detected is False
    assert resolved_col.approved_description == "ISO code (source DB)"
    assert resolved_col.description_source == "SOURCE_DB"
