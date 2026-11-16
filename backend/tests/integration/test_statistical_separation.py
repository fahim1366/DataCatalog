from __future__ import annotations

import uuid
import pytest
from sqlalchemy import desc, func, select
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
from backend.src.models.statistics import TableStatisticsHistory
from backend.src.models.sync_jobs import SyncJob, SyncJobStatus, SyncTriggerType
from backend.src.models.versioning import SchemaChangeLog, TableVersion
from backend.src.schemas.ingestion import (
    BulkIngestionRequest,
    IngestColumnPayload,
    IngestTablePayload,
)
from backend.src.services.auth.jwt_service import auth_service
from backend.src.services.ingestion.bulk_ingestion_service import bulk_ingestion_service


@pytest.fixture
async def setup_stats_env(test_db_session: AsyncSession):
    suffix = uuid.uuid4().hex[:8]

    user = User(
        id=uuid.uuid4(),
        username=f"stats_admin_{suffix}",
        email=f"stats_{suffix}@company.internal",
        hashed_password=auth_service.hash_password("Pass1234#"),
        full_name="Stats Admin",
        status=UserStatus.ACTIVE,
        is_superadmin=True,
    )
    test_db_session.add(user)

    connection = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"stats_pg_{suffix}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host="localhost",
        port=5432,
        database_name=f"stats_db_{suffix}",
        owner=user.username,
        kek_version=1,
        encrypted_dek=b"dummy_dek_32_bytes_stats_ingest",
        encrypted_credentials=b"dummy_credentials_stats_ingest",
    )
    test_db_session.add(connection)
    await test_db_session.flush()

    return {
        "user": user,
        "connection": connection,
        "suffix": suffix,
    }


@pytest.mark.asyncio
async def test_statistical_metrics_separation_from_structural_versioning(
    test_db_session: AsyncSession, setup_stats_env: dict
):
    """
    T060 Step A: Asserts that routine changes to row_count_estimate and size_bytes:
    1. Append records to table_statistics_history with sync_job_id.
    2. Update catalog_tables in place.
    3. Strictly DO NOT increment TableVersion.version_num.
    4. Record ZERO SchemaChangeLog entries.
    """
    connection = setup_stats_env["connection"]
    user = setup_stats_env["user"]
    suffix = setup_stats_env["suffix"]
    schema_name = f"telemetry_{suffix}"

    # Create SyncJob 1
    job_1 = SyncJob(
        id=uuid.uuid4(),
        connection_id=connection.id,
        status=SyncJobStatus.SUCCESS,
        triggered_by=SyncTriggerType.MANUAL,
    )
    test_db_session.add(job_1)
    await test_db_session.flush()

    # Initial Run 1: Ingest table orders with 2 columns and initial statistics
    req_1 = BulkIngestionRequest(
        connection_id=connection.id,
        schema_name=schema_name,
        sync_job_id=job_1.id,
        tables=[
            IngestTablePayload(
                name="orders",
                table_type=TableType.TABLE,
                row_count_estimate=1000,
                size_bytes=4096,
                source_description="Orders primary table",
                columns=[
                    IngestColumnPayload(
                        name="id",
                        ordinal_position=1,
                        data_type="integer",
                        raw_data_type="int4",
                        is_primary_key=True,
                    ),
                    IngestColumnPayload(
                        name="amount",
                        ordinal_position=2,
                        data_type="numeric(10,2)",
                        raw_data_type="numeric",
                        is_nullable=False,
                    ),
                ],
            )
        ],
    )

    resp_1 = await bulk_ingestion_service.ingest_bulk_metadata(
        session=test_db_session,
        payload=req_1,
        actor_id=user.id,
        actor_username=user.username,
        sync_job_id=job_1.id,
    )
    assert resp_1.success is True
    assert resp_1.versions_created == 1

    # Verify Table & Initial Version
    tbl_stmt = select(CatalogTable).where(
        CatalogTable.schema_id == resp_1.schema_id, CatalogTable.name == "orders"
    )
    table = (await test_db_session.execute(tbl_stmt)).scalar_one()
    assert table.row_count_estimate == 1000
    assert table.size_bytes == 4096

    v1_stmt = (
        select(TableVersion)
        .where(TableVersion.table_id == table.id)
        .order_by(desc(TableVersion.version_num))
    )
    v1 = (await test_db_session.execute(v1_stmt)).scalar_one()
    assert v1.version_num == 1

    # Verify initial statistics history record
    stats_stmt = (
        select(TableStatisticsHistory)
        .where(TableStatisticsHistory.table_id == table.id)
        .order_by(desc(TableStatisticsHistory.recorded_at))
    )
    stats_records = (await test_db_session.execute(stats_stmt)).scalars().all()
    assert len(stats_records) == 1
    assert stats_records[0].row_count_estimate == 1000
    assert stats_records[0].size_bytes == 4096
    assert stats_records[0].sync_job_id == job_1.id

    # Create SyncJob 2
    job_2 = SyncJob(
        id=uuid.uuid4(),
        connection_id=connection.id,
        status=SyncJobStatus.SUCCESS,
        triggered_by=SyncTriggerType.SCHEDULE,
    )
    test_db_session.add(job_2)
    await test_db_session.flush()

    # Run 2: Re-ingest orders with CHANGED statistics ONLY (row count 1000 -> 2500, size 4096 -> 8192)
    # Structural column definitions are 100% IDENTICAL
    req_2 = BulkIngestionRequest(
        connection_id=connection.id,
        schema_name=schema_name,
        sync_job_id=job_2.id,
        tables=[
            IngestTablePayload(
                name="orders",
                table_type=TableType.TABLE,
                row_count_estimate=2500,
                size_bytes=8192,
                source_description="Orders primary table",
                columns=[
                    IngestColumnPayload(
                        name="id",
                        ordinal_position=1,
                        data_type="integer",
                        raw_data_type="int4",
                        is_primary_key=True,
                    ),
                    IngestColumnPayload(
                        name="amount",
                        ordinal_position=2,
                        data_type="numeric(10,2)",
                        raw_data_type="numeric",
                        is_nullable=False,
                    ),
                ],
            )
        ],
    )

    resp_2 = await bulk_ingestion_service.ingest_bulk_metadata(
        session=test_db_session,
        payload=req_2,
        actor_id=user.id,
        actor_username=user.username,
        sync_job_id=job_2.id,
    )
    assert resp_2.success is True
    # Crucial assertion: 0 new TableVersions created for routine statistical changes
    assert resp_2.versions_created == 0

    # 1. table_statistics_history receives a new time-series record with sync_job_id
    stats_records_2 = (await test_db_session.execute(stats_stmt)).scalars().all()
    assert len(stats_records_2) == 2
    latest_stat = stats_records_2[0]
    assert latest_stat.row_count_estimate == 2500
    assert latest_stat.size_bytes == 8192
    assert latest_stat.sync_job_id == job_2.id

    # 2. catalog_tables updates in place
    await test_db_session.refresh(table)
    assert table.row_count_estimate == 2500
    assert table.size_bytes == 8192

    # 3. TableVersion.version_num does NOT increment
    all_versions = (await test_db_session.execute(v1_stmt)).scalars().all()
    assert len(all_versions) == 1
    assert all_versions[0].version_num == 1

    # 4. Zero structural SchemaChangeLog entries are recorded
    change_logs_stmt = select(SchemaChangeLog).where(SchemaChangeLog.table_id == table.id)
    change_logs = (await test_db_session.execute(change_logs_stmt)).scalars().all()
    assert len(change_logs) == 0

    # Run 3: Ingest with an ACTUAL structural DDL change (add column discount)
    req_3 = BulkIngestionRequest(
        connection_id=connection.id,
        schema_name=schema_name,
        sync_job_id=job_2.id,
        tables=[
            IngestTablePayload(
                name="orders",
                table_type=TableType.TABLE,
                row_count_estimate=3000,
                size_bytes=9000,
                columns=[
                    IngestColumnPayload(
                        name="id",
                        ordinal_position=1,
                        data_type="integer",
                        raw_data_type="int4",
                        is_primary_key=True,
                    ),
                    IngestColumnPayload(
                        name="amount",
                        ordinal_position=2,
                        data_type="numeric(10,2)",
                        raw_data_type="numeric",
                        is_nullable=False,
                    ),
                    IngestColumnPayload(
                        name="discount",
                        ordinal_position=3,
                        data_type="numeric(5,2)",
                        raw_data_type="numeric",
                        is_nullable=True,
                    ),
                ],
            )
        ],
    )
    resp_3 = await bulk_ingestion_service.ingest_bulk_metadata(
        session=test_db_session,
        payload=req_3,
        actor_id=user.id,
        actor_username=user.username,
        sync_job_id=job_2.id,
    )
    assert resp_3.versions_created == 1

    # Now TableVersion increments to 2
    all_versions_3 = (await test_db_session.execute(v1_stmt)).scalars().all()
    assert len(all_versions_3) == 2
    assert all_versions_3[0].version_num == 2

    # Exactly 1 SchemaChangeLog entry for the added column
    change_logs_3 = (await test_db_session.execute(change_logs_stmt)).scalars().all()
    assert len(change_logs_3) == 1
    assert change_logs_3[0].column_name == "discount"
    assert change_logs_3[0].change_type.value == "COLUMN_ADDED"
