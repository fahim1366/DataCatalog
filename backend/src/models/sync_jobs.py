from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.src.models.base import Base

if TYPE_CHECKING:
    from backend.src.models.connections import DatabaseConnection, SyncSchedule
    from backend.src.models.statistics import TableStatisticsHistory


class SyncJobStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    PARTIAL_FAILURE = "PARTIAL_FAILURE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class SyncTriggerType(str, enum.Enum):
    SCHEDULE = "SCHEDULE"
    MANUAL = "MANUAL"
    RETRY = "RETRY"


class SyncJob(Base):
    __tablename__ = "sync_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("database_connections.id", ondelete="CASCADE"),
        nullable=False,
    )
    schedule_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sync_schedules.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="QUEUED", server_default=text("'QUEUED'")
    )
    triggered_by: Mapped[str] = mapped_column(
        String(50), nullable=False, default="SCHEDULE", server_default=text("'SCHEDULE'")
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tables_scanned: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    tables_created: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    tables_updated: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    tables_deleted: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    columns_scanned: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    changes_detected: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_trace_jsonb: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("clock_timestamp()"),
    )

    connection: Mapped["DatabaseConnection"] = relationship(
        "DatabaseConnection", back_populates="sync_jobs"
    )
    schedule: Mapped[Optional["SyncSchedule"]] = relationship(
        "SyncSchedule", back_populates="sync_jobs"
    )
    statistics_history: Mapped[List["TableStatisticsHistory"]] = relationship(
        "TableStatisticsHistory",
        back_populates="sync_job",
    )

    __table_args__ = (
        Index("idx_sync_jobs_connection_started", "connection_id", text("started_at DESC")),
        Index("idx_sync_jobs_status", "status"),
        Index("idx_sync_jobs_created_at", text("created_at DESC")),
    )
