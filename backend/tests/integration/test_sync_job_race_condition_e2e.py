from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.database import async_session_factory, get_db_session
from backend.src.main import app
from backend.src.models.connections import DatabaseConnection, DatabaseEngine, SyncSchedule
from backend.src.models.identity import User, UserStatus
from backend.src.models.sync_jobs import SyncJob, SyncJobStatus, SyncTriggerType
from backend.src.services.auth.jwt_service import auth_service
from backend.src.services.connectors.base import (
    ColumnMetadata,
    SchemaExtractionResult,
    SchemaMetadata,
    TableMetadata,
)
from backend.src.services.crypto.envelope import EnvelopeEncryptionService
from backend.src.services.workers.tasks import execute_metadata_sync


@pytest.fixture
async def e2e_admin_auth(test_db_session: AsyncSession):
    suffix = uuid.uuid4().hex[:8]
    user = User(
        id=uuid.uuid4(),
        username=f"e2e_sync_admin_{suffix}",
        email=f"e2e_sync_admin_{suffix}@internal.corp",
        hashed_password=auth_service.hash_password("SuperSecret#2026"),
        full_name="E2E Sync Admin",
        status=UserStatus.ACTIVE,
        is_superadmin=True,
    )
    test_db_session.add(user)
    await test_db_session.commit()

    token = auth_service.create_access_token(
        data={
            "sub": str(user.id),
            "username": user.username,
            "email": user.email,
            "roles": ["SuperAdmin"],
            "is_superadmin": True,
        }
    )
    return {
        "user": user,
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"},
        "suffix": suffix,
    }


@pytest.fixture
async def e2e_connection(test_db_session: AsyncSession, e2e_admin_auth: dict):
    suffix = e2e_admin_auth["suffix"]
    crypto = EnvelopeEncryptionService()
    enc_dek, enc_creds = crypto.encrypt_credentials({"username": "postgres", "password": "password123"})

    conn = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"E2E_Conn_{suffix}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host="127.0.0.1",
        port=5432,
        database_name=f"e2e_db_{suffix}",
        default_schema="public",
        owner=e2e_admin_auth["user"].username,
        encrypted_dek=enc_dek,
        encrypted_credentials=enc_creds,
        is_active=True,
    )
    test_db_session.add(conn)

    sched = SyncSchedule(
        id=uuid.uuid4(),
        connection_id=conn.id,
        cron_expression="0 3 * * *",
        is_enabled=True,
        circuit_state="CLOSED",
        consecutive_failures=0,
    )
    test_db_session.add(sched)
    await test_db_session.commit()
    return conn, sched


@pytest.mark.asyncio
async def test_trigger_sync_commits_immediately_and_transitions_out_of_queued(
    test_db_session: AsyncSession, e2e_admin_auth: dict, e2e_connection: tuple
):
    """
    Verifies root cause fix:
    1. POST /api/v1/connections/{id}/sync commits the SyncJob row BEFORE dispatching background task.
    2. A distinct database session (simulating the background task / Celery worker) immediately sees the job.
    3. execute_metadata_sync runs in the background, transitions QUEUED -> RUNNING -> SUCCESS, sets started_at, completed_at, duration_ms.
    4. The job does NOT remain stuck in QUEUED.
    """
    conn, sched = e2e_connection

    # Mock extract_all_schemas to return a successful schema
    mock_schema = SchemaExtractionResult(
        schema=SchemaMetadata(name=f"public_{uuid.uuid4().hex[:6]}"),
        tables=[
            TableMetadata(
                schema_name="public",
                name="customers",
                table_type="TABLE",
                row_count_estimate=50,
                size_bytes=4096,
            )
        ],
        columns=[
            ColumnMetadata(
                schema_name="public",
                table_name="customers",
                name="id",
                ordinal_position=1,
                data_type="uuid",
                raw_data_type="uuid",
                is_primary_key=True,
            )
        ],
        success=True,
    )

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)

    try:
        with patch(
            "backend.src.services.connectors.postgres.PostgresConnector.extract_all_schemas",
            new_callable=AsyncMock,
            return_value=[mock_schema],
        ):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/api/v1/connections/{conn.id}/sync",
                    headers=e2e_admin_auth["headers"],
                )
                assert resp.status_code == 202, resp.text
                job_data = resp.json()
                job_id = uuid.UUID(job_data["id"])

                # 1. Verify that in an entirely separate session, the job is already committed & visible!
                async with async_session_factory() as independent_session:
                    stmt = select(SyncJob).where(SyncJob.id == job_id)
                    res = await independent_session.execute(stmt)
                    committed_job = res.scalar_one_or_none()
                    assert committed_job is not None, "SyncJob row was not committed before returning!"
                    assert committed_job.connection_id == conn.id

                # 2. Wait for the in-process background task (spawned by trigger_connection_sync) to complete
                final_job = None
                for _ in range(50):
                    await asyncio.sleep(0.1)
                    async with async_session_factory() as verify_session:
                        res_final = await verify_session.execute(select(SyncJob).where(SyncJob.id == job_id))
                        job_row = res_final.scalar_one_or_none()
                        if job_row and job_row.status in (SyncJobStatus.SUCCESS.value, SyncJobStatus.FAILED.value):
                            final_job = job_row
                            break

                assert final_job is not None, "Job did not complete within timeout"
                assert final_job.status == SyncJobStatus.SUCCESS.value
                assert final_job.started_at is not None
                assert final_job.completed_at is not None
                assert final_job.duration_ms is not None
                assert final_job.duration_ms >= 0
                assert final_job.tables_scanned >= 1
                assert final_job.error_message is None
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_execute_metadata_sync_exception_handling_transitions_to_failed(
    test_db_session: AsyncSession, e2e_connection: tuple
):
    """
    Verifies that any unexpected exception during execute_metadata_sync properly transitions
    the job to FAILED, sets error_message, completed_at, duration_ms, and does NOT leave it stuck in RUNNING.
    """
    conn, sched = e2e_connection

    job = SyncJob(
        id=uuid.uuid4(),
        connection_id=conn.id,
        schedule_id=sched.id,
        status=SyncJobStatus.QUEUED.value,
        triggered_by=SyncTriggerType.MANUAL.value,
        priority=10,
    )
    test_db_session.add(job)
    await test_db_session.commit()

    with patch(
        "backend.src.services.connectors.postgres.PostgresConnector.extract_all_schemas",
        new_callable=AsyncMock,
        side_effect=RuntimeError("Simulated unexpected crash during extraction"),
    ):
        result = await execute_metadata_sync(
            connection_id=conn.id,
            job_id=job.id,
        )

        assert result["status"] == SyncJobStatus.FAILED.value

    # Verify database state
    async with async_session_factory() as sess:
        stmt = select(SyncJob).where(SyncJob.id == job.id)
        res = await sess.execute(stmt)
        final_job = res.scalar_one()

        assert final_job.status == SyncJobStatus.FAILED.value
        assert final_job.started_at is not None
        assert final_job.completed_at is not None
        assert final_job.duration_ms is not None
        assert "Simulated unexpected crash" in (final_job.error_message or "")


@pytest.mark.asyncio
async def test_queue_auto_recovery_stale_job(
    test_db_session: AsyncSession, e2e_admin_auth: dict, e2e_connection: tuple
):
    """
    Verifies that GET /api/v1/sync-jobs/queue automatically recovers any QUEUED job
    waiting > 10s when worker_online is False.
    """
    conn, sched = e2e_connection

    # Create a job created 30 seconds ago in QUEUED status
    old_created_at = datetime.now(timezone.utc) - timedelta(seconds=30)
    stale_job = SyncJob(
        id=uuid.uuid4(),
        connection_id=conn.id,
        schedule_id=sched.id,
        status=SyncJobStatus.QUEUED.value,
        triggered_by=SyncTriggerType.MANUAL.value,
        priority=10,
        created_at=old_created_at,
    )
    test_db_session.add(stale_job)
    await test_db_session.commit()

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)

    dispatched_job_ids = []
    fake_sync = AsyncMock(side_effect=lambda connection_id, job_id: dispatched_job_ids.append(job_id))

    try:
        with patch("backend.src.api.v1.sync_jobs.has_active_celery_worker", return_value=False), \
             patch("backend.src.api.v1.sync_jobs.execute_metadata_sync", fake_sync):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    "/api/v1/sync-jobs/queue",
                    headers=e2e_admin_auth["headers"],
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["worker_online"] is False
                # Stale job was auto-recovered and dispatched
                await asyncio.sleep(0.05)  # Yield loop so create_task runs
                assert stale_job.id in dispatched_job_ids
    finally:
        app.dependency_overrides.pop(get_db_session, None)
