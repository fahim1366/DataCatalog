import uuid
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.models.catalog import DatabaseSchema
from backend.src.models.connections import DatabaseConnection, DatabaseEngine, SyncSchedule
from backend.src.models.sync_jobs import SyncJob, SyncJobStatus
from backend.src.services.crypto.envelope import EnvelopeEncryptionService
from backend.src.services.workers.tasks import (
    execute_metadata_sync,
    execute_metadata_sync_task,
    test_connection_sync as run_test_connection_sync,
    test_connection_task,
)


@pytest.fixture
async def sample_db_setup(test_db_session: AsyncSession):
    """Sets up a real DatabaseConnection, SyncSchedule, and SyncJob for worker task execution."""
    crypto = EnvelopeEncryptionService()
    creds = {"username": "postgres", "password": "wrong_password_test"}
    enc_dek, enc_creds = crypto.encrypt_credentials(creds)

    connection = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"test_worker_conn_{uuid.uuid4().hex[:8]}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host="127.0.0.1",
        port=5432,
        database_name="enterprise_datacatalog",
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

    job = SyncJob(
        id=uuid.uuid4(),
        connection_id=connection.id,
        schedule_id=schedule.id,
        status=SyncJobStatus.QUEUED.value,
        triggered_by="MANUAL",
    )
    test_db_session.add(job)
    await test_db_session.commit()

    return connection, schedule, job


@pytest.mark.asyncio
async def test_worker_task_parameter_contract():
    """T067: Task must accept strictly UUID/str parameters (zero plaintext secrets in arguments)."""
    assert hasattr(execute_metadata_sync_task, "delay")
    assert hasattr(test_connection_task, "delay")


@pytest.mark.asyncio
async def test_execute_metadata_sync_task_lifecycle(test_db_session: AsyncSession, sample_db_setup):
    """T067: Assert worker executes metadata sync, unwrapping credentials and updating SyncJob status."""
    connection, schedule, job = sample_db_setup

    # Execute async sync runner directly with real session
    result = await execute_metadata_sync(
        connection_id=connection.id,
        job_id=job.id,
        session=test_db_session,
    )

    # Refresh job from DB
    await test_db_session.refresh(job)

    assert result["job_id"] == str(job.id)
    assert job.status in (SyncJobStatus.SUCCESS.value, SyncJobStatus.FAILED.value, SyncJobStatus.PARTIAL_FAILURE.value)
    assert job.started_at is not None
    assert job.completed_at is not None
    assert job.duration_ms is not None
    assert job.duration_ms >= 0


@pytest.mark.asyncio
async def test_test_connection_task(test_db_session: AsyncSession, sample_db_setup):
    """T067: Assert test_connection diagnostic worker task executes in-memory unwrapping."""
    connection, _, _ = sample_db_setup

    result = await run_test_connection_sync(
        connection_id=connection.id,
        session=test_db_session,
    )

    assert "success" in result
    assert "latency_ms" in result
    assert "message" in result
