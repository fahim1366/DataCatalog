from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.src.models.base import Base
from backend.src.models.rbac import EntityType

if TYPE_CHECKING:
    from backend.src.models.connections import DatabaseConnection
    from backend.src.models.identity import User
    from backend.src.models.versioning import SchemaChangeLog, TableVersion


class TableType(str, enum.Enum):
    TABLE = "TABLE"
    VIEW = "VIEW"
    MATERIALIZED_VIEW = "MATERIALIZED_VIEW"
    EXTERNAL_TABLE = "EXTERNAL_TABLE"


class LifecycleStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    DEPRECATED = "DEPRECATED"
    DELETED = "DELETED"


class ClassificationLevel(str, enum.Enum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    PII = "PII"


class DatabaseSchema(Base):
    __tablename__ = "schemas"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("database_connections.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    owner: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[LifecycleStatus] = mapped_column(
        Enum(LifecycleStatus, name="lifecycle_status_enum", create_type=False),
        nullable=False,
        default=LifecycleStatus.ACTIVE,
        server_default=text("'ACTIVE'"),
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
        "DatabaseConnection", back_populates="schemas"
    )
    tables: Mapped[List["CatalogTable"]] = relationship(
        "CatalogTable",
        back_populates="schema",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("connection_id", "name", name="uq_connection_schema"),
    )


class CatalogTable(Base):
    __tablename__ = "catalog_tables"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    schema_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("schemas.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    table_type: Mapped[TableType] = mapped_column(
        Enum(TableType, name="table_type_enum", create_type=False),
        nullable=False,
        default=TableType.TABLE,
        server_default=text("'TABLE'"),
    )
    row_count_estimate: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    size_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    source_description: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    approved_description: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    effective_description: Mapped[Optional[str]] = mapped_column(
        Text,
        Computed(
            "COALESCE(NULLIF(source_description, ''), approved_description)",
            persisted=True,
        ),
        nullable=True,
    )
    owner: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[LifecycleStatus] = mapped_column(
        Enum(LifecycleStatus, name="lifecycle_status_enum", create_type=False),
        nullable=False,
        default=LifecycleStatus.ACTIVE,
        server_default=text("'ACTIVE'"),
    )
    custom_properties: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    search_vector: Mapped[Optional[str]] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('english', coalesce(name, '') || ' ' || coalesce(source_description, '') || ' ' || coalesce(approved_description, '') || ' ' || coalesce(owner, ''))",
            persisted=True,
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("clock_timestamp()"),
        onupdate=text("clock_timestamp()"),
    )

    schema: Mapped["DatabaseSchema"] = relationship(
        "DatabaseSchema", back_populates="tables"
    )
    columns: Mapped[List["CatalogColumn"]] = relationship(
        "CatalogColumn",
        back_populates="table",
        cascade="all, delete-orphan",
        order_by="CatalogColumn.ordinal_position",
    )
    versions: Mapped[List["TableVersion"]] = relationship(
        "TableVersion",
        back_populates="table",
        cascade="all, delete-orphan",
        order_by="desc(TableVersion.version_num)",
    )
    change_logs: Mapped[List["SchemaChangeLog"]] = relationship(
        "SchemaChangeLog",
        back_populates="table",
        cascade="all, delete-orphan",
        order_by="desc(SchemaChangeLog.created_at)",
    )

    __table_args__ = (
        UniqueConstraint("schema_id", "name", name="uq_schema_table"),
        Index(
            "idx_tables_keyset_schema",
            "schema_id",
            "name",
            "id",
            postgresql_where=text("status != 'DELETED'"),
        ),
        Index(
            "idx_tables_keyset_global",
            "name",
            "id",
            postgresql_where=text("status != 'DELETED'"),
        ),
        Index("idx_tables_search_vector_gin", "search_vector", postgresql_using="gin"),
        Index(
            "idx_tables_name_trgm_gin",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )


class CatalogColumn(Base):
    __tablename__ = "catalog_columns"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalog_tables.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    ordinal_position: Mapped[int] = mapped_column(Integer, nullable=False)
    data_type: Mapped[str] = mapped_column(String(100), nullable=False)
    raw_data_type: Mapped[str] = mapped_column(String(255), nullable=False)
    is_nullable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    is_primary_key: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    is_foreign_key: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    default_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_description: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    approved_description: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    effective_description: Mapped[Optional[str]] = mapped_column(
        Text,
        Computed(
            "COALESCE(NULLIF(source_description, ''), approved_description)",
            persisted=True,
        ),
        nullable=True,
    )
    classification_level: Mapped[ClassificationLevel] = mapped_column(
        Enum(
            ClassificationLevel,
            name="classification_level_enum",
            create_type=False,
        ),
        nullable=False,
        default=ClassificationLevel.INTERNAL,
        server_default=text("'INTERNAL'"),
    )
    status: Mapped[LifecycleStatus] = mapped_column(
        Enum(LifecycleStatus, name="lifecycle_status_enum", create_type=False),
        nullable=False,
        default=LifecycleStatus.ACTIVE,
        server_default=text("'ACTIVE'"),
    )
    search_vector: Mapped[Optional[str]] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('english', coalesce(name, '') || ' ' || coalesce(data_type, '') || ' ' || coalesce(source_description, '') || ' ' || coalesce(approved_description, ''))",
            persisted=True,
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("clock_timestamp()"),
        onupdate=text("clock_timestamp()"),
    )

    table: Mapped["CatalogTable"] = relationship(
        "CatalogTable", back_populates="columns"
    )

    __table_args__ = (
        UniqueConstraint("table_id", "name", name="uq_table_column"),
        Index(
            "idx_columns_keyset_table",
            "table_id",
            "ordinal_position",
            "id",
            postgresql_where=text("status != 'DELETED'"),
        ),
        Index(
            "idx_columns_classification",
            "classification_level",
            "table_id",
        ),
        Index(
            "idx_columns_search_vector_gin",
            "search_vector",
            postgresql_using="gin",
        ),
        Index(
            "idx_columns_name_trgm_gin",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    color: Mapped[str] = mapped_column(
        String(7),
        nullable=False,
        default="#1976D2",
        server_default=text("'#1976D2'"),
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_governed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )

    assignments: Mapped[List["TagAssignment"]] = relationship(
        "TagAssignment", back_populates="tag", cascade="all, delete-orphan"
    )


class TagAssignment(Base):
    __tablename__ = "tag_assignments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tags.id", ondelete="CASCADE"),
        nullable=False,
    )
    entity_type: Mapped[EntityType] = mapped_column(
        Enum(EntityType, name="entity_type_enum", create_type=False),
        nullable=False,
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    assigned_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )

    tag: Mapped["Tag"] = relationship("Tag", back_populates="assignments")
    assigner: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[assigned_by]
    )

    __table_args__ = (
        UniqueConstraint(
            "tag_id", "entity_type", "entity_id", name="uq_tag_assignment"
        ),
        Index(
            "idx_tag_assignments_lookup",
            "entity_type",
            "entity_id",
            "tag_id",
        ),
    )

