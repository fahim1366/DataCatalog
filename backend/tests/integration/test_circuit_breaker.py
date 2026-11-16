import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.models.connections import DatabaseConnection, DatabaseEngine, SyncSchedule
from backend.src.models.sync_jobs import SyncJob, SyncJobStatus
from backend.src.services.crypto.envelope import EnvelopeEncryptionService
from backend.src.services.scheduler.dynamic_beat import DatabaseScheduler
from backend.src.services.workers.circuit_breaker import circuit_breaker_manager, CircuitState
from backend.src.services.workers.tasks import execute_metadata_sync


@pytest.fixture
async def circuit_fixtures(test_db_session: AsyncSession):
    suffix = uuid.uuid4().hex[:8]
    crypto = EnvelopeEncryptionService()
    enc_dek, enc_creds = crypto.encrypt_credentials({"username": "postgres", "password": "unreachable_pass"})

    # Connection configured to unreachable test network IP (TEST-NET-1)
    connection = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"unreachable_conn_{suffix}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host="192.0.2.1",
        port=5432,
        database_name=f"unreachable_db_{suffix}",
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
async def test_circuit_breaker_tripping_and_scheduler_suppression(
    test_db_session: AsyncSession, circuit_fixtures
):
    """T070: Assert 3 consecutive failures trip circuit to OPEN, suppress Beat dispatching, and allow canary after cooldown."""
    connection, schedule = circuit_fixtures
    scheduler = DatabaseScheduler()

    # Mock connector failure simulating unreachable host 192.0.2.1 timeout
    with patch(
        "backend.src.services.connectors.postgres.PostgresConnector.extract_all_schemas",
        new_callable=AsyncMock,
        side_effect=TimeoutError("Network unreachable: 192.0.2.1:5432"),
    ):
        # Run 1: Failure 1
        job1 = SyncJob(id=uuid.uuid4(), connection_id=connection.id, schedule_id=schedule.id, status="QUEUED")
        test_db_session.add(job1)
        await test_db_session.commit()
        await execute_metadata_sync(connection.id, job1.id, session=test_db_session)

        await test_db_session.refresh(schedule)
        assert schedule.consecutive_failures == 1
        assert schedule.circuit_state == CircuitState.CLOSED.value

        # Run 2: Failure 2
        job2 = SyncJob(id=uuid.uuid4(), connection_id=connection.id, schedule_id=schedule.id, status="QUEUED")
        test_db_session.add(job2)
        await test_db_session.commit()
        await execute_metadata_sync(connection.id, job2.id, session=test_db_session)

        await test_db_session.refresh(schedule)
        assert schedule.consecutive_failures == 2
        assert schedule.circuit_state == CircuitState.CLOSED.value

        # Run 3: Failure 3 -> Trips to OPEN
        job3 = SyncJob(id=uuid.uuid4(), connection_id=connection.id, schedule_id=schedule.id, status="QUEUED")
        test_db_session.add(job3)
        await test_db_session.commit()
        await execute_metadata_sync(connection.id, job3.id, session=test_db_session)

        await test_db_session.refresh(schedule)
        assert schedule.consecutive_failures == 3
        assert schedule.circuit_state == CircuitState.OPEN.value
        assert schedule.cooldown_until is not None

        trip_time = schedule.cooldown_until - timedelta(minutes=15)

    # 4. Beat Scheduler during cooldown (5 minutes after trip): MUST SKIP
    during_cooldown_time = trip_time + timedelta(minutes=5)
    schedule.next_run_at = during_cooldown_time - timedelta(minutes=1)  # Due, but blocked
    await test_db_session.commit()

    dispatched = await scheduler.dispatch_due_schedules(
        session=test_db_session,
        current_time=during_cooldown_time,
        enqueue_tasks=False,
    )
    dispatched_ids = [d["schedule_id"] for d in dispatched]
    assert str(schedule.id) not in dispatched_ids

    # 5. Beat Scheduler after cooldown expiration (16 minutes after trip): CANARY MUST RUN
    after_cooldown_time = trip_time + timedelta(minutes=16)
    schedule.next_run_at = after_cooldown_time - timedelta(minutes=1)
    await test_db_session.commit()

    dispatched_canary = await scheduler.dispatch_due_schedules(
        session=test_db_session,
        current_time=after_cooldown_time,
        enqueue_tasks=False,
    )
    canary_ids = [d["schedule_id"] for d in dispatched_canary]
    assert str(schedule.id) in canary_ids

    # 6. Admin manual reset restores CLOSED
    await circuit_breaker_manager.reset_circuit_breaker(test_db_session, connection.id)
    await test_db_session.refresh(schedule)
    assert schedule.circuit_state == CircuitState.CLOSED.value
    assert schedule.consecutive_failures == 0
    assert schedule.cooldown_until is None
