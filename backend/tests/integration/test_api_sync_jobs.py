from __future__ import annotations

from datetime import datetime, timezone
import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.database import get_db_session
from backend.src.main import app
from backend.src.models.connections import DatabaseConnection, DatabaseEngine, SyncSchedule
from backend.src.models.identity import User, UserStatus
from backend.src.models.sync_jobs import SyncJob, SyncJobStatus, SyncTriggerType
from backend.src.services.auth.jwt_service import auth_service
from backend.src.services.crypto.envelope import EnvelopeEncryptionService


@pytest.fixture
async def admin_auth_data(test_db_session: AsyncSession):
    suffix = uuid.uuid4().hex[:8]
    admin_user = User(
        id=uuid.uuid4(),
        username=f"jobadmin_{suffix}",
        email=f"jobadmin_{suffix}@company.internal",
        hashed_password=auth_service.hash_password("SuperSecret#2026"),
        full_name="Job Admin",
        status=UserStatus.ACTIVE,
        is_superadmin=True,
    )
    test_db_session.add(admin_user)
    await test_db_session.flush()

    token = auth_service.create_access_token(
        data={
            "sub": str(admin_user.id),
            "username": admin_user.username,
            "email": admin_user.email,
            "roles": ["SuperAdmin"],
            "is_superadmin": True,
        }
    )
    return {
        "user": admin_user,
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"},
        "suffix": suffix,
    }


@pytest.fixture
async def sample_jobs_fixture(test_db_session: AsyncSession, admin_auth_data: dict):
    crypto = EnvelopeEncryptionService()
    enc_dek, enc_creds = crypto.encrypt_credentials({"username": "postgres", "password": "password"})

    conn = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"Job_Test_Conn_{admin_auth_data['suffix']}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host="127.0.0.1",
        port=5432,
        database_name="enterprise_datacatalog",
        default_schema="public",
        owner=admin_auth_data["user"].username,
        encrypted_dek=enc_dek,
        encrypted_credentials=enc_creds,
        is_active=True,
    )
    test_db_session.add(conn)

    schedule = SyncSchedule(
        id=uuid.uuid4(),
        connection_id=conn.id,
        cron_expression="0 2 * * *",
        is_enabled=True,
        circuit_state="CLOSED",
        consecutive_failures=0,
    )
    test_db_session.add(schedule)

    now = datetime.now(timezone.utc)
    job1 = SyncJob(
        id=uuid.uuid4(),
        connection_id=conn.id,
        schedule_id=schedule.id,
        status=SyncJobStatus.SUCCESS.value,
        triggered_by=SyncTriggerType.SCHEDULE.value,
        started_at=now,
        completed_at=now,
        duration_ms=450,
        tables_scanned=10,
        tables_created=2,
        tables_updated=1,
        tables_deleted=0,
        columns_scanned=80,
        changes_detected=3,
        error_message=None,
        error_trace_jsonb={},
    )
    job2 = SyncJob(
        id=uuid.uuid4(),
        connection_id=conn.id,
        schedule_id=schedule.id,
        status=SyncJobStatus.FAILED.value,
        triggered_by=SyncTriggerType.MANUAL.value,
        started_at=now,
        completed_at=now,
        duration_ms=120,
        tables_scanned=0,
        error_message="Authentication failed for user 'postgres'",
        error_trace_jsonb={
            "failed_schemas": [
                {"schema_name": "public", "error_code": "28P01", "error_message": "password authentication failed"}
            ],
            "exception_stack": "Traceback...",
        },
    )
    test_db_session.add(job1)
    test_db_session.add(job2)
    await test_db_session.commit()
    await test_db_session.refresh(conn)
    return conn, schedule, [job1, job2]


@pytest.mark.asyncio
async def test_get_sync_jobs_list_and_filters(
    test_db_session: AsyncSession, admin_auth_data: dict, sample_jobs_fixture: tuple
):
    conn, schedule, jobs = sample_jobs_fixture
    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Global list
            resp = await client.get("/api/v1/sync-jobs", headers=admin_auth_data["headers"])
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert "items" in data
            assert "total" in data
            assert data["total"] >= 2
            ids = [item["id"] for item in data["items"]]
            assert str(jobs[0].id) in ids
            assert str(jobs[1].id) in ids

            # 2. Filter by status=FAILED
            resp_filter = await client.get(
                "/api/v1/sync-jobs?status=FAILED",
                headers=admin_auth_data["headers"],
            )
            assert resp_filter.status_code == 200
            data_filter = resp_filter.json()
            assert all(item["status"] == "FAILED" for item in data_filter["items"])

            # 3. Filter by connection_id
            resp_conn = await client.get(
                f"/api/v1/sync-jobs?connection_id={conn.id}",
                headers=admin_auth_data["headers"],
            )
            assert resp_conn.status_code == 200
            data_conn = resp_conn.json()
            assert data_conn["total"] == 2
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_get_sync_job_detail(
    test_db_session: AsyncSession, admin_auth_data: dict, sample_jobs_fixture: tuple
):
    conn, schedule, jobs = sample_jobs_fixture
    failed_job = jobs[1]

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/sync-jobs/{failed_job.id}",
                headers=admin_auth_data["headers"],
            )
            assert resp.status_code == 200, resp.text
            detail = resp.json()
            assert detail["id"] == str(failed_job.id)
            assert detail["status"] == "FAILED"
            assert detail["connection_id"] == str(conn.id)
            assert "error_trace_jsonb" in detail
            assert "failed_schemas" in detail["error_trace_jsonb"]
            assert len(detail["error_trace_jsonb"]["failed_schemas"]) == 1

            # 404 for non-existent job
            resp_404 = await client.get(
                f"/api/v1/sync-jobs/{uuid.uuid4()}",
                headers=admin_auth_data["headers"],
            )
            assert resp_404.status_code == 404
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_retry_sync_job(
    test_db_session: AsyncSession, admin_auth_data: dict, sample_jobs_fixture: tuple
):
    conn, schedule, jobs = sample_jobs_fixture
    failed_job = jobs[1]

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/sync-jobs/{failed_job.id}/retry",
                headers=admin_auth_data["headers"],
            )
            assert resp.status_code == 202, resp.text
            retry_job = resp.json()
            assert retry_job["connection_id"] == str(conn.id)
            assert retry_job["status"] == "QUEUED"
            assert retry_job["triggered_by"] == "RETRY"

            # 404 for non-existent job
            resp_404 = await client.post(
                f"/api/v1/sync-jobs/{uuid.uuid4()}/retry",
                headers=admin_auth_data["headers"],
            )
            assert resp_404.status_code == 404
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_get_connection_jobs_history(
    test_db_session: AsyncSession, admin_auth_data: dict, sample_jobs_fixture: tuple
):
    conn, schedule, jobs = sample_jobs_fixture

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/connections/{conn.id}/jobs",
                headers=admin_auth_data["headers"],
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["total"] == 2
            assert len(data["items"]) == 2

            # 404 for non-existent connection
            resp_404 = await client.get(
                f"/api/v1/connections/{uuid.uuid4()}/jobs",
                headers=admin_auth_data["headers"],
            )
            assert resp_404.status_code == 404
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_retry_sync_job_rbac_forbidden_for_viewer(
    test_db_session: AsyncSession, sample_jobs_fixture: tuple
):
    conn, schedule, jobs = sample_jobs_fixture
    failed_job = jobs[1]
    suffix = uuid.uuid4().hex[:8]

    viewer_user = User(
        id=uuid.uuid4(),
        username=f"retry_viewer_{suffix}",
        email=f"retry_viewer_{suffix}@company.internal",
        hashed_password=auth_service.hash_password("ViewerPass123!"),
        full_name="Retry Viewer",
        status=UserStatus.ACTIVE,
        is_superadmin=False,
    )
    test_db_session.add(viewer_user)
    await test_db_session.flush()

    token = auth_service.create_access_token(
        data={
            "sub": str(viewer_user.id),
            "username": viewer_user.username,
            "email": viewer_user.email,
            "roles": ["Viewer"],
            "is_superadmin": False,
        }
    )
    viewer_headers = {"Authorization": f"Bearer {token}"}

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/sync-jobs/{failed_job.id}/retry",
                headers=viewer_headers,
            )
            assert resp.status_code == 403
            assert "Forbidden" in resp.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_sync_fallback_in_process_when_celery_broker_offline(
    test_db_session: AsyncSession, admin_auth_data: dict, sample_jobs_fixture: tuple, monkeypatch
):
    from unittest.mock import AsyncMock, patch
    from backend.src.services.workers.tasks import manual_metadata_sync_task

    conn, schedule, jobs = sample_jobs_fixture
    failed_job = jobs[1]

    # Mock apply_async to simulate Redis / Celery offline failure
    def mock_apply_async(*args, **kwargs):
        raise ConnectionError("Redis broker is unreachable at 127.0.0.1:6379")

    monkeypatch.setattr(manual_metadata_sync_task, "apply_async", mock_apply_async)

    # Mock execute_metadata_sync so we can verify BackgroundTasks invoked it
    fallback_mock = AsyncMock(return_value={"success": True})
    monkeypatch.setattr("backend.src.api.v1.sync_jobs.execute_metadata_sync", fallback_mock)
    monkeypatch.setattr("backend.src.api.v1.connections.execute_metadata_sync", fallback_mock)

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Test retry fallback
            resp = await client.post(
                f"/api/v1/sync-jobs/{failed_job.id}/retry",
                headers=admin_auth_data["headers"],
            )
            assert resp.status_code == 202
            retry_data = resp.json()
            assert retry_data["status"] == "QUEUED"
            assert retry_data["triggered_by"] == "RETRY"
            # Background task executed
            assert fallback_mock.called

            # Mark any active jobs as SUCCESS so concurrency guard allows triggering sync
            res = await test_db_session.execute(
                select(SyncJob).where(
                    SyncJob.connection_id == conn.id,
                    SyncJob.status.in_([SyncJobStatus.QUEUED.value, SyncJobStatus.RUNNING.value]),
                )
            )
            for j in res.scalars().all():
                j.status = SyncJobStatus.SUCCESS.value
            await test_db_session.commit()

            # 2. Test trigger connection sync fallback
            fallback_mock.reset_mock()
            resp2 = await client.post(
                f"/api/v1/connections/{conn.id}/sync",
                headers=admin_auth_data["headers"],
            )
            assert resp2.status_code == 202
            sync_data = resp2.json()
            assert sync_data["status"] == "QUEUED"
            assert sync_data["triggered_by"] == "MANUAL"
            assert fallback_mock.called
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_get_sync_queue_visibility_and_eta(
    test_db_session: AsyncSession, admin_auth_data: dict, sample_jobs_fixture: tuple
):
    conn, schedule, jobs = sample_jobs_fixture

    # Clean up any existing active jobs for a deterministic queue state
    stmt_active = select(SyncJob).where(
        SyncJob.status.in_([SyncJobStatus.QUEUED.value, SyncJobStatus.RUNNING.value])
    )
    res_active = await test_db_session.execute(stmt_active)
    for aj in res_active.scalars().all():
        aj.status = SyncJobStatus.CANCELLED.value
    await test_db_session.commit()

    now = datetime.now(timezone.utc)
    # Add a successful job with duration_ms = 20000 to establish historical average (20s)
    success_job = SyncJob(
        id=uuid.uuid4(),
        connection_id=conn.id,
        schedule_id=schedule.id,
        status=SyncJobStatus.SUCCESS.value,
        triggered_by=SyncTriggerType.SCHEDULE.value,
        started_at=now,
        completed_at=now,
        duration_ms=20000,
        tables_scanned=10,
        created_at=now,
    )
    test_db_session.add(success_job)

    # 1. Add a running job
    running_job = SyncJob(
        id=uuid.uuid4(),
        connection_id=conn.id,
        schedule_id=schedule.id,
        status=SyncJobStatus.RUNNING.value,
        triggered_by=SyncTriggerType.SCHEDULE.value,
        started_at=now,
        created_at=now,
    )
    test_db_session.add(running_job)

    # 2. Add queued job with default priority (0)
    queued_job_normal = SyncJob(
        id=uuid.uuid4(),
        connection_id=conn.id,
        schedule_id=schedule.id,
        status=SyncJobStatus.QUEUED.value,
        triggered_by=SyncTriggerType.SCHEDULE.value,
        priority=0,
        created_at=now,
    )
    test_db_session.add(queued_job_normal)

    # 3. Add queued job with high priority (10)
    queued_job_expedited = SyncJob(
        id=uuid.uuid4(),
        connection_id=conn.id,
        schedule_id=schedule.id,
        status=SyncJobStatus.QUEUED.value,
        triggered_by=SyncTriggerType.MANUAL.value,
        priority=10,
        created_at=now,
    )
    test_db_session.add(queued_job_expedited)
    await test_db_session.commit()

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/sync-jobs/queue",
                headers=admin_auth_data["headers"],
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "worker_online" in data
            assert isinstance(data["worker_online"], bool)

            # Check running jobs
            assert len(data["running_jobs"]) >= 1
            running_ids = [j["id"] for j in data["running_jobs"]]
            assert str(running_job.id) in running_ids

            # Check queued jobs: expedited (priority 10) must be first (position 1)
            queued_ids = [j["id"] for j in data["queued_jobs"]]
            assert str(queued_job_expedited.id) in queued_ids
            assert str(queued_job_normal.id) in queued_ids

            expedited_idx = queued_ids.index(str(queued_job_expedited.id))
            normal_idx = queued_ids.index(str(queued_job_normal.id))
            assert expedited_idx < normal_idx  # Higher priority comes first!

            expedited_item = data["queued_jobs"][expedited_idx]
            normal_item = data["queued_jobs"][normal_idx]

            assert expedited_item["queue_position"] == expedited_idx + 1
            assert normal_item["queue_position"] == normal_idx + 1
            assert expedited_item["estimated_wait_seconds"] is not None
            assert normal_item["estimated_wait_seconds"] is not None
            assert expedited_item["estimated_wait_seconds"] < normal_item["estimated_wait_seconds"]
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_expedite_sync_job_out_of_order(
    test_db_session: AsyncSession, admin_auth_data: dict, sample_jobs_fixture: tuple, monkeypatch
):
    from unittest.mock import AsyncMock
    from backend.src.models.audit import AuditLog

    conn, schedule, _ = sample_jobs_fixture

    now = datetime.now(timezone.utc)
    queued_job = SyncJob(
        id=uuid.uuid4(),
        connection_id=conn.id,
        schedule_id=schedule.id,
        status=SyncJobStatus.QUEUED.value,
        triggered_by=SyncTriggerType.SCHEDULE.value,
        priority=0,
        created_at=now,
    )
    test_db_session.add(queued_job)
    await test_db_session.commit()

    fallback_mock = AsyncMock(return_value={"success": True})
    monkeypatch.setattr("backend.src.api.v1.sync_jobs.execute_metadata_sync", fallback_mock)

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/sync-jobs/{queued_job.id}/expedite",
                headers=admin_auth_data["headers"],
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["id"] == str(queued_job.id)
            assert data["priority"] == 10
            assert data["triggered_by"] == "MANUAL"

            # Verify audit log
            audit_stmt = select(AuditLog).where(
                AuditLog.action == "EXPEDITE_SYNC_JOB",
                AuditLog.entity_id == conn.id,
            )
            audit_res = await test_db_session.execute(audit_stmt)
            audit_entries = list(audit_res.scalars().all())
            assert len(audit_entries) >= 1
            assert any(entry.diff_jsonb.get("priority") == 10 for entry in audit_entries)

            # Try to expedite a non-queued job (should fail with 400)
            queued_job.status = SyncJobStatus.SUCCESS.value
            await test_db_session.commit()

            resp_bad = await client.post(
                f"/api/v1/sync-jobs/{queued_job.id}/expedite",
                headers=admin_auth_data["headers"],
            )
            assert resp_bad.status_code == 400
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_cancel_sync_job_and_release_lock(
    test_db_session: AsyncSession, admin_auth_data: dict, sample_jobs_fixture: tuple
):
    from backend.src.models.audit import AuditLog

    conn, schedule, _ = sample_jobs_fixture

    # Clean active jobs on conn
    stmt_active = select(SyncJob).where(
        SyncJob.connection_id == conn.id,
        SyncJob.status.in_([SyncJobStatus.QUEUED.value, SyncJobStatus.RUNNING.value]),
    )
    for j in (await test_db_session.execute(stmt_active)).scalars().all():
        j.status = SyncJobStatus.CANCELLED.value
    await test_db_session.commit()

    now = datetime.now(timezone.utc)
    queued_job = SyncJob(
        id=uuid.uuid4(),
        connection_id=conn.id,
        schedule_id=schedule.id,
        status=SyncJobStatus.QUEUED.value,
        triggered_by=SyncTriggerType.MANUAL.value,
        created_at=now,
    )
    test_db_session.add(queued_job)
    await test_db_session.commit()

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. While queued, another manual sync request should be rejected by concurrency guard (409)
            resp_conflict = await client.post(
                f"/api/v1/connections/{conn.id}/sync",
                headers=admin_auth_data["headers"],
            )
            assert resp_conflict.status_code == 409

            # 2. Cancel the queued job
            resp_cancel = await client.post(
                f"/api/v1/sync-jobs/{queued_job.id}/cancel",
                headers=admin_auth_data["headers"],
            )
            assert resp_cancel.status_code == 200
            data = resp_cancel.json()
            assert data["id"] == str(queued_job.id)
            assert data["status"] == "CANCELLED"
            assert data["error_message"] == "لغو شده توسط کاربر"
            assert data["completed_at"] is not None

            # Verify audit log
            audit_stmt = select(AuditLog).where(
                AuditLog.action == "CANCEL_SYNC_JOB",
                AuditLog.entity_id == conn.id,
            )
            audit_res = await test_db_session.execute(audit_stmt)
            audit_entries = list(audit_res.scalars().all())
            assert len(audit_entries) >= 1
            assert any(entry.diff_jsonb.get("status") == "CANCELLED" for entry in audit_entries)

            # 3. Concurrency lock is now released: another sync can run!
            resp_new_sync = await client.post(
                f"/api/v1/connections/{conn.id}/sync",
                headers=admin_auth_data["headers"],
            )
            assert resp_new_sync.status_code == 202

            # 4. Attempting to cancel already CANCELLED job should return 400
            resp_repeat_cancel = await client.post(
                f"/api/v1/sync-jobs/{queued_job.id}/cancel",
                headers=admin_auth_data["headers"],
            )
            assert resp_repeat_cancel.status_code == 400
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_batch_expedite_sync(
    test_db_session: AsyncSession, admin_auth_data: dict, sample_jobs_fixture: tuple, monkeypatch
):
    from unittest.mock import AsyncMock

    conn, schedule, _ = sample_jobs_fixture

    fallback_mock = AsyncMock(return_value={"success": True})
    monkeypatch.setattr("backend.src.api.v1.sync_jobs.execute_metadata_sync", fallback_mock)

    # Clean active jobs on conn
    stmt_active = select(SyncJob).where(
        SyncJob.connection_id == conn.id,
        SyncJob.status.in_([SyncJobStatus.QUEUED.value, SyncJobStatus.RUNNING.value]),
    )
    for j in (await test_db_session.execute(stmt_active)).scalars().all():
        j.status = SyncJobStatus.CANCELLED.value
    await test_db_session.commit()

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/sync-jobs/batch-expedite",
                headers=admin_auth_data["headers"],
                json={"connection_ids": [str(conn.id)]},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["total_dispatched"] == 1
            assert len(data["jobs"]) == 1
            assert data["jobs"][0]["priority"] == 10
            assert data["jobs"][0]["triggered_by"] == "MANUAL"
    finally:
        app.dependency_overrides.pop(get_db_session, None)


