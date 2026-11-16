from __future__ import annotations

import uuid
import pytest
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.models.catalog import (
    CatalogColumn,
    CatalogTable,
    ClassificationLevel,
    LifecycleStatus,
    TableType,
)
from backend.src.models.connections import DatabaseConnection, DatabaseEngine
from backend.src.models.identity import User, UserStatus
from backend.src.models.versioning import SchemaChangeLog, SchemaChangeType, TableVersion
from backend.src.schemas.ingestion import (
    BulkIngestionRequest,
    IngestColumnPayload,
    IngestTablePayload,
)
from backend.src.services.auth.jwt_service import auth_service
from backend.src.services.ingestion.bulk_ingestion_service import bulk_ingestion_service


@pytest.fixture
async def setup_ddl_env(test_db_session: AsyncSession):
    suffix = uuid.uuid4().hex[:8]

    user = User(
        id=uuid.uuid4(),
        username=f"ddl_admin_{suffix}",
        email=f"ddl_{suffix}@company.internal",
        hashed_password=auth_service.hash_password("Pass1234#"),
        full_name="DDL Admin",
        status=UserStatus.ACTIVE,
        is_superadmin=True,
    )
    test_db_session.add(user)

    connection = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"ddl_pg_{suffix}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host="localhost",
        port=5432,
        database_name=f"ddl_db_{suffix}",
        owner=user.username,
        kek_version=1,
        encrypted_dek=b"dummy_dek_32_bytes_ddl_ingest",
        encrypted_credentials=b"dummy_credentials_ddl_ingest",
    )
    test_db_session.add(connection)
    await test_db_session.flush()

    return {
        "user": user,
        "connection": connection,
        "suffix": suffix,
    }


@pytest.mark.asyncio
async def test_structural_ddl_versioning_vs_nonstructural_modifications(
    test_db_session: AsyncSession, setup_ddl_env: dict
):
    """
    T063: Asserts that:
    1. Structural DDL mutations (COLUMN_ADDED, TYPE_CHANGED, COLUMN_DROPPED) increment
       TableVersion.version_num and record exact SchemaChangeLog entries.
    2. Non-structural modifications (comments, statistics updates) DO NOT increment
       TableVersion.version_num and record zero structural change logs.
    """
    connection = setup_ddl_env["connection"]
    user = setup_ddl_env["user"]
    suffix = setup_ddl_env["suffix"]
    schema_name = f"supply_chain_{suffix}"

    # Step 1: Initial Ingestion - Baseline v1
    req_v1 = BulkIngestionRequest(
        connection_id=connection.id,
        schema_name=schema_name,
        tables=[
            IngestTablePayload(
                name="inventory",
                table_type=TableType.TABLE,
                row_count_estimate=500,
                size_bytes=2048,
                source_description="Baseline inventory table",
                columns=[
                    IngestColumnPayload(
                        name="id",
                        ordinal_position=1,
                        data_type="integer",
                        raw_data_type="int4",
                        is_primary_key=True,
                    ),
                    IngestColumnPayload(
                        name="sku",
                        ordinal_position=2,
                        data_type="varchar(64)",
                        raw_data_type="varchar",
                    ),
                    IngestColumnPayload(
                        name="quantity",
                        ordinal_position=3,
                        data_type="integer",
                        raw_data_type="int4",
                    ),
                ],
            )
        ],
    )
    resp_v1 = await bulk_ingestion_service.ingest_bulk_metadata(
        session=test_db_session,
        payload=req_v1,
        actor_id=user.id,
        actor_username=user.username,
    )
    assert resp_v1.versions_created == 1

    tbl_stmt = select(CatalogTable).where(
        CatalogTable.schema_id == resp_v1.schema_id, CatalogTable.name == "inventory"
    )
    table = (await test_db_session.execute(tbl_stmt)).scalar_one()

    # Step 2: DDL Mutation 1 - COLUMN_ADDED (warehouse_location)
    req_v2 = BulkIngestionRequest(
        connection_id=connection.id,
        schema_name=schema_name,
        tables=[
            IngestTablePayload(
                name="inventory",
                columns=[
                    IngestColumnPayload(name="id", ordinal_position=1, data_type="integer", raw_data_type="int4", is_primary_key=True),
                    IngestColumnPayload(name="sku", ordinal_position=2, data_type="varchar(64)", raw_data_type="varchar"),
                    IngestColumnPayload(name="quantity", ordinal_position=3, data_type="integer", raw_data_type="int4"),
                    IngestColumnPayload(name="warehouse_location", ordinal_position=4, data_type="varchar(128)", raw_data_type="varchar"),
                ],
            )
        ],
    )
    resp_v2 = await bulk_ingestion_service.ingest_bulk_metadata(
        session=test_db_session,
        payload=req_v2,
        actor_id=user.id,
        actor_username=user.username,
    )
    assert resp_v2.versions_created == 1

    # Verify v2 version and change log
    v2_log_stmt = select(SchemaChangeLog).where(
        SchemaChangeLog.table_id == table.id,
        SchemaChangeLog.change_type == SchemaChangeType.COLUMN_ADDED,
    )
    v2_log = (await test_db_session.execute(v2_log_stmt)).scalar_one()
    assert v2_log.column_name == "warehouse_location"

    # Step 3: DDL Mutation 2 - TYPE_CHANGED (quantity integer -> bigint)
    req_v3 = BulkIngestionRequest(
        connection_id=connection.id,
        schema_name=schema_name,
        tables=[
            IngestTablePayload(
                name="inventory",
                columns=[
                    IngestColumnPayload(name="id", ordinal_position=1, data_type="integer", raw_data_type="int4", is_primary_key=True),
                    IngestColumnPayload(name="sku", ordinal_position=2, data_type="varchar(64)", raw_data_type="varchar"),
                    IngestColumnPayload(name="quantity", ordinal_position=3, data_type="bigint", raw_data_type="int8"),
                    IngestColumnPayload(name="warehouse_location", ordinal_position=4, data_type="varchar(128)", raw_data_type="varchar"),
                ],
            )
        ],
    )
    resp_v3 = await bulk_ingestion_service.ingest_bulk_metadata(
        session=test_db_session,
        payload=req_v3,
        actor_id=user.id,
        actor_username=user.username,
    )
    assert resp_v3.versions_created == 1

    v3_log_stmt = select(SchemaChangeLog).where(
        SchemaChangeLog.table_id == table.id,
        SchemaChangeLog.change_type == SchemaChangeType.TYPE_CHANGED,
    )
    v3_log = (await test_db_session.execute(v3_log_stmt)).scalar_one()
    assert v3_log.column_name == "quantity"
    assert v3_log.old_value["data_type"] == "integer"
    assert v3_log.new_value["data_type"] == "bigint"

    # Step 4: DDL Mutation 3 - COLUMN_DROPPED (warehouse_location removed)
    req_v4 = BulkIngestionRequest(
        connection_id=connection.id,
        schema_name=schema_name,
        tables=[
            IngestTablePayload(
                name="inventory",
                columns=[
                    IngestColumnPayload(name="id", ordinal_position=1, data_type="integer", raw_data_type="int4", is_primary_key=True),
                    IngestColumnPayload(name="sku", ordinal_position=2, data_type="varchar(64)", raw_data_type="varchar"),
                    IngestColumnPayload(name="quantity", ordinal_position=3, data_type="bigint", raw_data_type="int8"),
                ],
            )
        ],
    )
    resp_v4 = await bulk_ingestion_service.ingest_bulk_metadata(
        session=test_db_session,
        payload=req_v4,
        actor_id=user.id,
        actor_username=user.username,
    )
    assert resp_v4.versions_created == 1

    v4_log_stmt = select(SchemaChangeLog).where(
        SchemaChangeLog.table_id == table.id,
        SchemaChangeLog.change_type == SchemaChangeType.COLUMN_DROPPED,
    )
    v4_log = (await test_db_session.execute(v4_log_stmt)).scalar_one()
    assert v4_log.column_name == "warehouse_location"

    # Verify latest version is exactly 4
    latest_v_stmt = (
        select(TableVersion)
        .where(TableVersion.table_id == table.id)
        .order_by(desc(TableVersion.version_num))
    )
    versions = (await test_db_session.execute(latest_v_stmt)).scalars().all()
    assert len(versions) == 4
    assert versions[0].version_num == 4

    # Step 5: Non-structural modifications - Comments change ONLY
    req_comments = BulkIngestionRequest(
        connection_id=connection.id,
        schema_name=schema_name,
        tables=[
            IngestTablePayload(
                name="inventory",
                source_description="Altered source comment for inventory",
                columns=[
                    IngestColumnPayload(name="id", ordinal_position=1, data_type="integer", raw_data_type="int4", is_primary_key=True, source_description="Updated id comment"),
                    IngestColumnPayload(name="sku", ordinal_position=2, data_type="varchar(64)", raw_data_type="varchar", source_description="Updated sku comment"),
                    IngestColumnPayload(name="quantity", ordinal_position=3, data_type="bigint", raw_data_type="int8", source_description="Updated quantity comment"),
                ],
            )
        ],
    )
    resp_comments = await bulk_ingestion_service.ingest_bulk_metadata(
        session=test_db_session,
        payload=req_comments,
        actor_id=user.id,
        actor_username=user.username,
    )
    assert resp_comments.versions_created == 0

    # Step 6: Non-structural modifications - Statistics change ONLY
    req_stats = BulkIngestionRequest(
        connection_id=connection.id,
        schema_name=schema_name,
        tables=[
            IngestTablePayload(
                name="inventory",
                row_count_estimate=99999,
                size_bytes=888888,
                columns=[
                    IngestColumnPayload(name="id", ordinal_position=1, data_type="integer", raw_data_type="int4", is_primary_key=True),
                    IngestColumnPayload(name="sku", ordinal_position=2, data_type="varchar(64)", raw_data_type="varchar"),
                    IngestColumnPayload(name="quantity", ordinal_position=3, data_type="bigint", raw_data_type="int8"),
                ],
            )
        ],
    )
    resp_stats = await bulk_ingestion_service.ingest_bulk_metadata(
        session=test_db_session,
        payload=req_stats,
        actor_id=user.id,
        actor_username=user.username,
    )
    assert resp_stats.versions_created == 0

    # TableVersion MUST remain at version_num = 4
    versions_final = (await test_db_session.execute(latest_v_stmt)).scalars().all()
    assert len(versions_final) == 4
    assert versions_final[0].version_num == 4
