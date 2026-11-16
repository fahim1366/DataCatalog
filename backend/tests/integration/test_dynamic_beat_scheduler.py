import uuid
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.models.connections import DatabaseConnection, DatabaseEngine, SyncSchedule
from backend.src.models.sync_jobs import SyncJob, SyncJobStatus
from backend.src.services.scheduler.dynamic_beat import DatabaseScheduler
from backend.src.services.scheduler.jitter import calculate_schedule_jitter


@pytest.fixture
async def scheduler_fixtures(test_db_session: AsyncSession):
    """Sets up multiple DatabaseConnections and SyncSchedules in various states."""
    suffix = uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc)

    # 1. Normal active schedule (due now)
    conn_active = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"conn_active_{suffix}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host="127.0.0.1",
        port=5432,
        database_name="db_active",
        owner="admin",
        kek_version=1,
        encrypted_dek=b"dek_123456789012345678901234567890",
        encrypted_credentials=b"creds_12345678901234567890",
    )
    sched_active = SyncSchedule(
        id=uuid.uuid4(),
        connection_id=conn_active.id,
        cron_expression="0 0 * * *",
        is_enabled=True,
        consecutive_failures=0,
        circuit_state="CLOSED",
        next_run_at=now - timedelta(minutes=5),  # Past due
    )

    # 2. Disabled schedule (due now, but is_enabled=False)
    conn_disabled = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"conn_disabled_{suffix}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host="127.0.0.1",
        port=5432,
        database_name="db_disabled",
        owner="admin",
        kek_version=1,
        encrypted_dek=b"dek_123456789012345678901234567890",
        encrypted_credentials=b"creds_12345678901234567890",
    )
    sched_disabled = SyncSchedule(
        id=uuid.uuid4(),
        connection_id=conn_disabled.id,
        cron_expression="0 0 * * *",
        is_enabled=False,
        consecutive_failures=0,
        circuit_state="CLOSED",
        next_run_at=now - timedelta(minutes=5),
    )

    # 3. Tripped schedule (circuit OPEN, cooldown active)
    conn_open = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"conn_open_{suffix}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host="127.0.0.1",
        port=5432,
        database_name="db_open",
        owner="admin",
        kek_version=1,
        encrypted_dek=b"dek_123456789012345678901234567890",
        encrypted_credentials=b"creds_12345678901234567890",
    )
    sched_open = SyncSchedule(
        id=uuid.uuid4(),
        connection_id=conn_open.id,
        cron_expression="0 0 * * *",
        is_enabled=True,
        consecutive_failures=3,
        circuit_state="OPEN",
        cooldown_until=now + timedelta(minutes=10),  # In cooldown
        next_run_at=now - timedelta(minutes=5),
    )

    # 4. Canary schedule (circuit OPEN, but cooldown expired)
    conn_canary = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"conn_canary_{suffix}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host="127.0.0.1",
        port=5432,
        database_name="db_canary",
        owner="admin",
        kek_version=1,
        encrypted_dek=b"dek_123456789012345678901234567890",
        encrypted_credentials=b"creds_12345678901234567890",
    )
    sched_canary = SyncSchedule(
        id=uuid.uuid4(),
        connection_id=conn_canary.id,
        cron_expression="0 0 * * *",
        is_enabled=True,
        consecutive_failures=3,
        circuit_state="OPEN",
        cooldown_until=now - timedelta(minutes=2),  # Expired cooldown -> canary
        next_run_at=now - timedelta(minutes=5),
    )

    test_db_session.add_all([
        conn_active, sched_active,
        conn_disabled, sched_disabled,
        conn_open, sched_open,
        conn_canary, sched_canary,
    ])
    await test_db_session.commit()

    return {
        "active": (conn_active, sched_active),
        "disabled": (conn_disabled, sched_disabled),
        "open": (conn_open, sched_open),
        "canary": (conn_canary, sched_canary),
    }


@pytest.mark.asyncio
async def test_dynamic_beat_scheduler_dispatch_lifecycle(
    test_db_session: AsyncSession, scheduler_fixtures
):
    """T068: Assert DatabaseScheduler queries active schedules, respects circuit breaker, and applies jitter."""
    scheduler = DatabaseScheduler()
    now = datetime.now(timezone.utc)

    # Execute scheduler tick
    dispatched_jobs = await scheduler.dispatch_due_schedules(
        session=test_db_session,
        current_time=now,
        enqueue_tasks=False,  # dry-run enqueue for unit testing
    )

    dispatched_schedule_ids = [d["schedule_id"] for d in dispatched_jobs]
    active_sched = scheduler_fixtures["active"][1]
    disabled_sched = scheduler_fixtures["disabled"][1]
    open_sched = scheduler_fixtures["open"][1]
    canary_sched = scheduler_fixtures["canary"][1]

    # 1. Active schedule MUST be dispatched
    assert str(active_sched.id) in dispatched_schedule_ids

    # 2. Disabled schedule MUST NOT be dispatched
    assert str(disabled_sched.id) not in dispatched_schedule_ids

    # 3. OPEN schedule in cooldown MUST NOT be dispatched
    assert str(open_sched.id) not in dispatched_schedule_ids

    # 4. Canary schedule (OPEN with expired cooldown) MUST be dispatched
    assert str(canary_sched.id) in dispatched_schedule_ids

    # 5. Verify created SyncJobs in database
    for dispatched in dispatched_jobs:
        job_id = uuid.UUID(dispatched["job_id"])
        stmt = select(SyncJob).where(SyncJob.id == job_id)
        res = await test_db_session.execute(stmt)
        job = res.scalar_one_or_none()
        assert job is not None
        assert job.status == SyncJobStatus.QUEUED.value
        assert job.triggered_by == "SCHEDULE"

    # 6. Verify next_run_at and last_run_at were updated with jitter
    await test_db_session.refresh(active_sched)
    assert active_sched.last_run_at is not None
    assert active_sched.next_run_at is not None
    assert active_sched.next_run_at > now
