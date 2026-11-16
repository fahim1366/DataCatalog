import time
import uuid
from datetime import datetime, timezone
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import patch, AsyncMock

from backend.src.models.catalog import DatabaseSchema
from backend.src.models.connections import DatabaseConnection, DatabaseEngine, SyncSchedule
from backend.src.models.sync_jobs import SyncJob, SyncJobStatus
from backend.src.services.connectors.base import (
    ColumnMetadata,
    SchemaExtractionResult,
    SchemaMetadata,
    TableMetadata,
)
from backend.src.services.crypto.envelope import EnvelopeEncryptionService
from backend.src.services.workers.tasks import execute_metadata_sync


@pytest.fixture
async def lifecycle_fixtures(test_db_session: AsyncSession):
    suffix = uuid.uuid4().hex[:8]
    crypto = EnvelopeEncryptionService()
    enc_dek, enc_creds = crypto.encrypt_credentials({"username": "postgres", "password": "password123"})

    connection = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"lifecycle_conn_{suffix}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host="127.0.0.1",
        port=5432,
        database_name=f"lifecycle_db_{suffix}",
        owner="admin",
        kek_version=1,
        encrypted_dek=enc_dek,
        encrypted_credentials=enc_creds,
        custom_params={},
    )
    test_db_session.add(connection)

    schedule = SyncSchedule(
        id=uuid.uuid4(),
        connection_id=connection.id,
        cron_expression="0 0 * * *",
        is_enabled=True,
        consecutive_failures=0,
        circuit_state="CLOSED",
    )
    test_db_session.add(schedule)
    await test_db_session.commit()

    return connection, schedule


@pytest.mark.asyncio
async def test_sync_job_partial_failure_error_trace_capture(
    test_db_session: AsyncSession, lifecycle_fixtures
):
    """T069: Assert partial failure transitions status to PARTIAL_FAILURE and captures error codes in error_trace_jsonb."""
    connection, schedule = lifecycle_fixtures

    job = SyncJob(
        id=uuid.uuid4(),
        connection_id=connection.id,
        schedule_id=schedule.id,
        status=SyncJobStatus.QUEUED.value,
        triggered_by="SCHEDULE",
    )
    test_db_session.add(job)
    await test_db_session.commit()

    # Simulate one successful schema and one failing schema with error code
    mock_success = SchemaExtractionResult(
        schema=SchemaMetadata(name=f"healthy_schema_{uuid.uuid4().hex[:6]}"),
        tables=[
            TableMetadata(
                schema_name="healthy_schema",
                name="users",
                table_type="TABLE",
                row_count_estimate=100,
                size_bytes=8192,
            )
        ],
        columns=[
            ColumnMetadata(
                schema_name="healthy_schema",
                table_name="users",
                name="id",
                ordinal_position=1,
                data_type="uuid",
                raw_data_type="uuid",
                is_primary_key=True,
            )
        ],
        success=True,
    )

    mock_failed = SchemaExtractionResult(
        schema=SchemaMetadata(name="restricted_schema"),
        tables=[],
        columns=[],
        success=False,
        error_message="permission denied for schema restricted_schema",
        error_code="42501",
    )

    with patch(
        "backend.src.services.connectors.postgres.PostgresConnector.extract_all_schemas",
        new_callable=AsyncMock,
        return_value=[mock_success, mock_failed],
    ):
        result = await execute_metadata_sync(
            connection_id=connection.id,
            job_id=job.id,
            session=test_db_session,
        )

    await test_db_session.refresh(job)

    assert result["status"] == SyncJobStatus.PARTIAL_FAILURE.value
    assert job.status == SyncJobStatus.PARTIAL_FAILURE.value
    assert "restricted_schema" in result["failed_schemas"]
    assert "restricted_schema" in job.error_trace_jsonb
    assert job.error_trace_jsonb["restricted_schema"]["error_code"] == "42501"
    assert "permission denied" in job.error_trace_jsonb["restricted_schema"]["error_message"]
    assert job.tables_scanned >= 1
    assert job.columns_scanned >= 1
    assert job.duration_ms >= 0


@pytest.mark.asyncio
async def test_sync_job_full_failure_and_latency(
    test_db_session: AsyncSession, lifecycle_fixtures
):
    """T069: Assert full failure transitions status to FAILED and commits state under 100ms."""
    connection, schedule = lifecycle_fixtures

    job = SyncJob(
        id=uuid.uuid4(),
        connection_id=connection.id,
        schedule_id=schedule.id,
        status=SyncJobStatus.QUEUED.value,
        triggered_by="SCHEDULE",
    )
    test_db_session.add(job)
    await test_db_session.commit()

    with patch(
        "backend.src.services.connectors.postgres.PostgresConnector.extract_all_schemas",
        new_callable=AsyncMock,
        side_effect=ConnectionRefusedError("Connection refused by peer"),
    ):
        start_time = time.perf_counter()
        result = await execute_metadata_sync(
            connection_id=connection.id,
            job_id=job.id,
            session=test_db_session,
        )
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

    await test_db_session.refresh(job)
    await test_db_session.refresh(schedule)

    assert result["status"] == SyncJobStatus.FAILED.value
    assert job.status == SyncJobStatus.FAILED.value
    assert "Connection refused" in job.error_message
    assert schedule.consecutive_failures == 1

    # Verify status update latency is under 100ms
    assert elapsed_ms < 100.0, f"Status update latency {elapsed_ms:.2f}ms exceeded 100ms ceiling"
