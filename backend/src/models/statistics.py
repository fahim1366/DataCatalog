from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.src.models.base import Base

if TYPE_CHECKING:
    from backend.src.models.catalog import CatalogTable
    from backend.src.models.sync_jobs import SyncJob


class TableStatisticsHistory(Base):
    __tablename__ = "table_statistics_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog_tables.id", ondelete="CASCADE"),
        nullable=False,
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("clock_timestamp()"),
    )
    row_count_estimate: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    size_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    sync_job_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sync_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )

    table: Mapped["CatalogTable"] = relationship(
        "CatalogTable", back_populates="statistics_history"
    )
    sync_job: Mapped[Optional["SyncJob"]] = relationship(
        "SyncJob", back_populates="statistics_history"
    )

    __table_args__ = (
        Index("idx_table_stats_table_time", "table_id", text("recorded_at DESC")),
        Index("idx_table_stats_sync_job", "sync_job_id"),
    )
