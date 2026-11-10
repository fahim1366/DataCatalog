from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.src.models.base import Base

if TYPE_CHECKING:
    from backend.src.models.catalog import CatalogTable
    from backend.src.models.identity import User


class SchemaChangeType(str, enum.Enum):
    COLUMN_ADDED = "COLUMN_ADDED"
    COLUMN_DROPPED = "COLUMN_DROPPED"
    TYPE_CHANGED = "TYPE_CHANGED"
    NULLABILITY_CHANGED = "NULLABILITY_CHANGED"
    DESCRIPTION_UPDATED = "DESCRIPTION_UPDATED"
    TAG_ASSIGNED = "TAG_ASSIGNED"
    TAG_REMOVED = "TAG_REMOVED"


class TableVersion(Base):
    __tablename__ = "table_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog_tables.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_num: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_jsonb: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )

    table: Mapped["CatalogTable"] = relationship(
        "CatalogTable", back_populates="versions"
    )
    creator: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[created_by]
    )
    change_logs: Mapped[List["SchemaChangeLog"]] = relationship(
        "SchemaChangeLog",
        back_populates="version",
    )

    __table_args__ = (
        UniqueConstraint("table_id", "version_num", name="uq_table_version"),
    )


class SchemaChangeLog(Base):
    __tablename__ = "schema_change_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog_tables.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("table_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    change_type: Mapped[SchemaChangeType] = mapped_column(
        Enum(
            SchemaChangeType,
            name="schema_change_type_enum",
            create_type=False,
        ),
        nullable=False,
    )
    column_name: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    old_value: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    new_value: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )

    table: Mapped["CatalogTable"] = relationship(
        "CatalogTable", back_populates="change_logs"
    )
    version: Mapped[Optional["TableVersion"]] = relationship(
        "TableVersion", back_populates="change_logs"
    )
    actor: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[actor_id]
    )

