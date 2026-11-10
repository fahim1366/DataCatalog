from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.src.models.base import Base

if TYPE_CHECKING:
    from backend.src.models.catalog import DatabaseSchema


class DatabaseEngine(str, enum.Enum):
    POSTGRESQL = "POSTGRESQL"
    MSSQL = "MSSQL"
    ORACLE = "ORACLE"
    MYSQL = "MYSQL"
    CLICKHOUSE = "CLICKHOUSE"


class DatabaseConnection(Base):
    __tablename__ = "database_connections"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    engine_type: Mapped[DatabaseEngine] = mapped_column(
        Enum(DatabaseEngine, name="database_engine_enum", create_type=False),
        nullable=False,
    )
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    database_name: Mapped[str] = mapped_column(String(255), nullable=False)
    default_schema: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )
    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    kek_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    encrypted_dek: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    encrypted_credentials: Mapped[bytes] = mapped_column(
        LargeBinary, nullable=False
    )
    custom_params: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("clock_timestamp()"),
        onupdate=text("clock_timestamp()"),
    )

    sync_schedule: Mapped[Optional["SyncSchedule"]] = relationship(
        "SyncSchedule",
        back_populates="connection",
        uselist=False,
        cascade="all, delete-orphan",
    )
    schemas: Mapped[List["DatabaseSchema"]] = relationship(
        "DatabaseSchema",
        back_populates="connection",
        cascade="all, delete-orphan",
    )


class SyncSchedule(Base):
    __tablename__ = "sync_schedules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("database_connections.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    cron_expression: Mapped[str] = mapped_column(String(100), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    last_run_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_run_status: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )
    last_run_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    next_run_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("clock_timestamp()"),
        onupdate=text("clock_timestamp()"),
    )

    connection: Mapped["DatabaseConnection"] = relationship(
        "DatabaseConnection", back_populates="sync_schedule"
    )

