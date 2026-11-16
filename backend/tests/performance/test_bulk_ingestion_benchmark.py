from __future__ import annotations

import time
import uuid
import pytest
from sqlalchemy import func, select
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
from backend.src.models.sync_jobs import SyncJob, SyncJobStatus, SyncTriggerType
from backend.src.schemas.ingestion import (
    BulkIngestionRequest,
    IngestColumnPayload,
    IngestTablePayload,
)
from backend.src.services.auth.jwt_service import auth_service
from backend.src.services.ingestion.bulk_ingestion_service import bulk_ingestion_service


@pytest.fixture
async def benchmark_env(test_db_session: AsyncSession):
    suffix = uuid.uuid4().hex[:8]

    user = User(
        id=uuid.uuid4(),
        username=f"bench_admin_{suffix}",
        email=f"bench_{suffix}@company.internal",
        hashed_password=auth_service.hash_password("Pass1234#"),
        full_name="Benchmark Administrator",
        status=UserStatus.ACTIVE,
        is_superadmin=True,
    )
    test_db_session.add(user)

    connection = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"bench_pg_{suffix}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host="localhost",
        port=5432,
        database_name=f"bench_db_{suffix}",
        owner=user.username,
        kek_version=1,
        encrypted_dek=b"dummy_dek_32_bytes_bench_ingest",
        encrypted_credentials=b"dummy_credentials_bench_ingest",
    )
    test_db_session.add(connection)
    await test_db_session.flush()

    return {
        "user": user,
        "connection": connection,
        "suffix": suffix,
    }


@pytest.mark.asyncio
async def test_bulk_ingestion_200_tables_5000_columns_under_3s_sla(
    test_db_session: AsyncSession, benchmark_env: dict
):
    """
    T062: Performance benchmark asserting that full metadata ingestion of:
    - 200 catalog tables
    - 5,000 catalog columns (25 columns per table)
    - Complete statistics history persistence
    - Structural versioning evaluation
    completes in < 3.0s total elapsed execution time against real PostgreSQL.
    """
    connection = benchmark_env["connection"]
    user = benchmark_env["user"]
    suffix = benchmark_env["suffix"]
    schema_name = f"benchmark_schema_{suffix}"

    job = SyncJob(
        id=uuid.uuid4(),
        connection_id=connection.id,
        status=SyncJobStatus.RUNNING,
        triggered_by=SyncTriggerType.SCHEDULE,
    )
    test_db_session.add(job)
    await test_db_session.flush()

    num_tables = 200
    cols_per_table = 25
    tables: list[IngestTablePayload] = []

    for t_idx in range(num_tables):
        cols = [
            IngestColumnPayload(
                name=f"col_{c_idx:02d}",
                ordinal_position=c_idx + 1,
                data_type="bigint" if c_idx == 0 else "varchar(255)",
                raw_data_type="int8" if c_idx == 0 else "varchar",
                is_nullable=c_idx != 0,
                is_primary_key=c_idx == 0,
                is_foreign_key=False,
                default_value="0" if c_idx == 0 else None,
                source_description=f"Field comment {c_idx} for table {t_idx}",
                classification_level=ClassificationLevel.INTERNAL,
            )
            for c_idx in range(cols_per_table)
        ]
        tables.append(
            IngestTablePayload(
                name=f"perf_table_{t_idx:03d}",
                table_type=TableType.TABLE,
                row_count_estimate=50000 + t_idx,
                size_bytes=1048576 * (t_idx + 1),
                source_description=f"Performance benchmark table {t_idx}",
                owner="bi_engineering",
                custom_properties={"tier": "silver", "index": t_idx},
                columns=cols,
            )
        )

    request = BulkIngestionRequest(
        connection_id=connection.id,
        schema_name=schema_name,
        schema_description="High-Throughput Enterprise SLA Benchmark",
        tables=tables,
        sync_job_id=job.id,
    )

    # Measure elapsed time for initial ingestion
    start_time = time.perf_counter()
    response_1 = await bulk_ingestion_service.ingest_bulk_metadata(
        session=test_db_session,
        payload=request,
        actor_id=user.id,
        actor_username=user.username,
        sync_job_id=job.id,
    )
    elapsed_initial = time.perf_counter() - start_time

    print(f"\n[BENCHMARK] Initial Ingestion of 200 tables / 5,000 columns: {elapsed_initial:.3f}s")
    assert elapsed_initial < 3.0, f"Initial ingestion took {elapsed_initial:.3f}s, exceeding 3.0s SLA!"
    assert response_1.tables_upserted == num_tables
    assert response_1.columns_upserted == (num_tables * cols_per_table)

    # Measure elapsed time for subsequent update ingestion (diff / update / stats time-series)
    start_time_update = time.perf_counter()
    response_2 = await bulk_ingestion_service.ingest_bulk_metadata(
        session=test_db_session,
        payload=request,
        actor_id=user.id,
        actor_username=user.username,
        sync_job_id=job.id,
    )
    elapsed_update = time.perf_counter() - start_time_update

    print(f"[BENCHMARK] Subsequent Update Ingestion of 200 tables / 5,000 columns: {elapsed_update:.3f}s")
    assert elapsed_update < 3.0, f"Subsequent update ingestion took {elapsed_update:.3f}s, exceeding 3.0s SLA!"
    assert response_2.tables_upserted == num_tables

    # Data verification
    tbl_count = (
        await test_db_session.execute(
            select(func.count(CatalogTable.id)).where(
                CatalogTable.schema_id == response_1.schema_id,
                CatalogTable.status == LifecycleStatus.ACTIVE,
            )
        )
    ).scalar()
    assert tbl_count == num_tables

    col_count = (
        await test_db_session.execute(
            select(func.count(CatalogColumn.id))
            .join(CatalogTable, CatalogColumn.table_id == CatalogTable.id)
            .where(
                CatalogTable.schema_id == response_1.schema_id,
                CatalogColumn.status == LifecycleStatus.ACTIVE,
            )
        )
    ).scalar()
    assert col_count == (num_tables * cols_per_table)
