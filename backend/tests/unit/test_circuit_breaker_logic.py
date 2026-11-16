import uuid
from datetime import datetime, timedelta, timezone
import pytest

from backend.src.models.connections import SyncSchedule
from backend.src.services.workers.circuit_breaker import (
    CircuitBreakerManager,
    CircuitState,
)


def create_test_schedule(
    failures: int = 0,
    circuit_state: str = "CLOSED",
    cooldown_until: datetime = None,
    is_enabled: bool = True,
) -> SyncSchedule:
    return SyncSchedule(
        id=uuid.uuid4(),
        connection_id=uuid.uuid4(),
        cron_expression="0 * * * *",
        is_enabled=is_enabled,
        consecutive_failures=failures,
        circuit_state=circuit_state,
        cooldown_until=cooldown_until,
    )


def test_circuit_breaker_initial_closed_state():
    """T066: Assert normal initial state is CLOSED and allowed to execute."""
    cb = CircuitBreakerManager()
    schedule = create_test_schedule()

    assert cb.get_effective_state(schedule) == CircuitState.CLOSED
    assert cb.can_execute_sync(schedule) is True


def test_circuit_breaker_trips_to_open_after_3_failures():
    """T066: Assert 3 consecutive failures trip circuit to OPEN with 15m cooldown."""
    cb = CircuitBreakerManager()
    schedule = create_test_schedule()
    now = datetime(2026, 11, 16, 12, 0, 0, tzinfo=timezone.utc)

    # 1st failure
    state1 = cb.record_failure(schedule, error_message="Error 1", current_time=now)
    assert state1 == CircuitState.CLOSED
    assert schedule.consecutive_failures == 1
    assert schedule.circuit_state == "CLOSED"
    assert schedule.cooldown_until is None

    # 2nd failure
    state2 = cb.record_failure(schedule, error_message="Error 2", current_time=now)
    assert state2 == CircuitState.CLOSED
    assert schedule.consecutive_failures == 2
    assert schedule.circuit_state == "CLOSED"
    assert schedule.cooldown_until is None

    # 3rd failure -> trips to OPEN
    state3 = cb.record_failure(schedule, error_message="Error 3", current_time=now)
    assert state3 == CircuitState.OPEN
    assert schedule.consecutive_failures == 3
    assert schedule.circuit_state == "OPEN"
    assert schedule.cooldown_until == now + timedelta(minutes=15)
    assert cb.can_execute_sync(schedule, current_time=now) is False


def test_circuit_breaker_transitions_to_half_open_when_cooldown_expires():
    """T066: Assert OPEN transitions to HALF_OPEN once cooldown_until passes."""
    cb = CircuitBreakerManager()
    now = datetime(2026, 11, 16, 12, 0, 0, tzinfo=timezone.utc)
    cooldown = now + timedelta(minutes=15)
    schedule = create_test_schedule(failures=3, circuit_state="OPEN", cooldown_until=cooldown)

    # 10 minutes in: still OPEN and blocked
    assert cb.get_effective_state(schedule, current_time=now + timedelta(minutes=10)) == CircuitState.OPEN
    assert cb.can_execute_sync(schedule, current_time=now + timedelta(minutes=10)) is False

    # 15 minutes in (exact cooldown): transitions to HALF_OPEN and allowed as canary
    assert cb.get_effective_state(schedule, current_time=cooldown) == CircuitState.HALF_OPEN
    assert cb.can_execute_sync(schedule, current_time=cooldown) is True

    # 20 minutes in: transitions to HALF_OPEN
    assert cb.get_effective_state(schedule, current_time=now + timedelta(minutes=20)) == CircuitState.HALF_OPEN
    assert cb.can_execute_sync(schedule, current_time=now + timedelta(minutes=20)) is True


def test_circuit_breaker_canary_success_resets_to_closed():
    """T066: Assert canary success in HALF_OPEN resets state to CLOSED and consecutive_failures=0."""
    cb = CircuitBreakerManager()
    now = datetime(2026, 11, 16, 12, 0, 0, tzinfo=timezone.utc)
    cooldown = now + timedelta(minutes=15)
    schedule = create_test_schedule(failures=3, circuit_state="OPEN", cooldown_until=cooldown)

    # Cooldown has passed -> canary runs at 16 minutes
    canary_time = now + timedelta(minutes=16)
    new_state = cb.record_success(schedule, current_time=canary_time)

    assert new_state == CircuitState.CLOSED
    assert schedule.circuit_state == "CLOSED"
    assert schedule.consecutive_failures == 0
    assert schedule.cooldown_until is None
    assert schedule.last_run_status == "SUCCESS"
    assert cb.can_execute_sync(schedule, current_time=canary_time) is True


def test_circuit_breaker_canary_failure_refreshes_open_cooldown():
    """T066: Assert canary failure in HALF_OPEN re-opens circuit with fresh 15m cooldown."""
    cb = CircuitBreakerManager()
    now = datetime(2026, 11, 16, 12, 0, 0, tzinfo=timezone.utc)
    cooldown = now + timedelta(minutes=15)
    schedule = create_test_schedule(failures=3, circuit_state="OPEN", cooldown_until=cooldown)

    # Canary fails at 16 minutes
    canary_time = now + timedelta(minutes=16)
    new_state = cb.record_failure(schedule, error_message="Canary failed", current_time=canary_time)

    assert new_state == CircuitState.OPEN
    assert schedule.circuit_state == "OPEN"
    assert schedule.consecutive_failures == 4
    assert schedule.cooldown_until == canary_time + timedelta(minutes=15)
    assert cb.can_execute_sync(schedule, current_time=canary_time) is False


def test_circuit_breaker_manual_admin_reset():
    """T066: Assert manual admin reset unconditionally forces CLOSED and clears failures."""
    cb = CircuitBreakerManager()
    now = datetime(2026, 11, 16, 12, 0, 0, tzinfo=timezone.utc)
    cooldown = now + timedelta(minutes=15)
    schedule = create_test_schedule(failures=5, circuit_state="OPEN", cooldown_until=cooldown)

    reset_state = cb.manual_reset(schedule)
    assert reset_state == CircuitState.CLOSED
    assert schedule.circuit_state == "CLOSED"
    assert schedule.consecutive_failures == 0
    assert schedule.cooldown_until is None
    assert cb.can_execute_sync(schedule, current_time=now) is True


def test_disabled_schedule_suppresses_sync():
    """T066: Assert is_enabled=False prevents execution regardless of circuit state."""
    cb = CircuitBreakerManager()
    schedule = create_test_schedule(is_enabled=False)
    assert cb.can_execute_sync(schedule) is False
