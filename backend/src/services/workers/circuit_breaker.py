from __future__ import annotations

import enum
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.models.connections import SyncSchedule

logger = logging.getLogger(__name__)


class CircuitState(str, enum.Enum):
    """3-State Circuit Breaker lifecycle states."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreakerManager:
    """
    Manages the 3-state Circuit Breaker state machine for DatabaseConnection sync schedules.

    Rules:
    - CLOSED: Normal operation. Scheduled runs proceed without restriction.
    - OPEN: Tripped when consecutive_failures >= 3. Scheduled runs are suppressed for 15 minutes.
    - HALF_OPEN: Entered automatically when cooldown_until has passed. Next execution serves as a canary:
        * Canary Success: Circuit transitions to CLOSED, consecutive_failures resets to 0.
        * Canary Failure: Circuit remains/returns to OPEN with refreshed 15-minute cooldown.
    - Manual Reset: Admin can unconditionally force the circuit to CLOSED and clear failure counters.
    """

    FAILURE_THRESHOLD: int = 3
    COOLDOWN_MINUTES: int = 15

    def get_effective_state(
        self, schedule: SyncSchedule, current_time: Optional[datetime] = None
    ) -> CircuitState:
        """
        Calculates the effective circuit state at current_time.
        If stored state is OPEN and cooldown has passed, returns HALF_OPEN.
        """
        now = current_time or datetime.now(timezone.utc)
        if schedule.circuit_state == CircuitState.OPEN.value or schedule.circuit_state == CircuitState.OPEN:
            if schedule.cooldown_until is None or now >= schedule.cooldown_until:
                return CircuitState.HALF_OPEN
            return CircuitState.OPEN

        if schedule.circuit_state == CircuitState.HALF_OPEN.value or schedule.circuit_state == CircuitState.HALF_OPEN:
            return CircuitState.HALF_OPEN

        return CircuitState.CLOSED

    def can_execute_sync(
        self, schedule: SyncSchedule, current_time: Optional[datetime] = None
    ) -> bool:
        """Determines if a sync job can be dispatched under the circuit breaker rules."""
        if not schedule.is_enabled:
            return False

        effective_state = self.get_effective_state(schedule, current_time)
        return effective_state in (CircuitState.CLOSED, CircuitState.HALF_OPEN)

    def record_success(
        self, schedule: SyncSchedule, current_time: Optional[datetime] = None
    ) -> CircuitState:
        """
        Records a successful sync execution.
        Resets consecutive_failures to 0 and transitions circuit to CLOSED.
        """
        now = current_time or datetime.now(timezone.utc)
        schedule.consecutive_failures = 0
        schedule.circuit_state = CircuitState.CLOSED.value
        schedule.cooldown_until = None
        schedule.last_run_at = now
        schedule.last_run_status = "SUCCESS"
        logger.info(
            "Circuit breaker for schedule %s reset to CLOSED after successful sync.",
            schedule.id,
        )
        return CircuitState.CLOSED

    def record_failure(
        self,
        schedule: SyncSchedule,
        error_message: Optional[str] = None,
        current_time: Optional[datetime] = None,
    ) -> CircuitState:
        """
        Records a failed sync execution.
        Increments consecutive_failures and trips to OPEN if threshold reached or canary failed.
        """
        now = current_time or datetime.now(timezone.utc)
        effective_before = self.get_effective_state(schedule, now)
        schedule.consecutive_failures += 1
        schedule.last_run_at = now
        schedule.last_run_status = "FAILED"
        if error_message:
            schedule.last_run_message = error_message

        if effective_before == CircuitState.HALF_OPEN or schedule.consecutive_failures >= self.FAILURE_THRESHOLD:
            schedule.circuit_state = CircuitState.OPEN.value
            schedule.cooldown_until = now + timedelta(minutes=self.COOLDOWN_MINUTES)
            logger.warning(
                "Circuit breaker for schedule %s tripped to OPEN. Cooldown until %s. Failures: %d",
                schedule.id,
                schedule.cooldown_until,
                schedule.consecutive_failures,
            )
            return CircuitState.OPEN
        else:
            schedule.circuit_state = CircuitState.CLOSED.value
            logger.info(
                "Circuit breaker for schedule %s recorded failure %d/%d (state remains CLOSED).",
                schedule.id,
                schedule.consecutive_failures,
                self.FAILURE_THRESHOLD,
            )
            return CircuitState.CLOSED

    def manual_reset(self, schedule: SyncSchedule) -> CircuitState:
        """Manually forces the circuit breaker to CLOSED and clears failure counts."""
        schedule.consecutive_failures = 0
        schedule.circuit_state = CircuitState.CLOSED.value
        schedule.cooldown_until = None
        logger.info("Admin manual reset executed for circuit breaker on schedule %s.", schedule.id)
        return CircuitState.CLOSED

    async def get_schedule_by_connection_id(
        self, session: AsyncSession, connection_id: uuid.UUID
    ) -> Optional[SyncSchedule]:
        """Fetches the SyncSchedule for a given connection_id."""
        stmt = select(SyncSchedule).where(SyncSchedule.connection_id == connection_id)
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

    async def record_job_outcome(
        self,
        session: AsyncSession,
        connection_id: uuid.UUID,
        success: bool,
        error_message: Optional[str] = None,
    ) -> Optional[SyncSchedule]:
        """Updates circuit breaker state in the database based on job outcome."""
        schedule = await self.get_schedule_by_connection_id(session, connection_id)
        if not schedule:
            return None

        if success:
            self.record_success(schedule)
        else:
            self.record_failure(schedule, error_message=error_message)

        await session.commit()
        await session.refresh(schedule)
        return schedule

    async def reset_circuit_breaker(
        self, session: AsyncSession, connection_id: uuid.UUID
    ) -> Optional[SyncSchedule]:
        """Executes manual admin reset in the database."""
        schedule = await self.get_schedule_by_connection_id(session, connection_id)
        if not schedule:
            return None

        self.manual_reset(schedule)
        await session.commit()
        await session.refresh(schedule)
        return schedule


circuit_breaker_manager = CircuitBreakerManager()
