from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.src.models.base import Base


class PermissionAction(str, enum.Enum):
    VIEW_METADATA = "VIEW_METADATA"
    EDIT_DESCRIPTION = "EDIT_DESCRIPTION"
    MANAGE_TAGS = "MANAGE_TAGS"
    MANAGE_CONNECTIONS = "MANAGE_CONNECTIONS"
    VIEW_AUDIT_LOGS = "VIEW_AUDIT_LOGS"
    EXPORT_REPORTS = "EXPORT_REPORTS"


class PermissionEffect(str, enum.Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"


class ResourceType(str, enum.Enum):
    GLOBAL = "GLOBAL"
    DATABASE = "DATABASE"
    SCHEMA = "SCHEMA"
    TABLE = "TABLE"
    COLUMN = "COLUMN"
    CATEGORY = "CATEGORY"


class EntityType(str, enum.Enum):
    DATABASE = "DATABASE"
    SCHEMA = "SCHEMA"
    TABLE = "TABLE"
    COLUMN = "COLUMN"


class PermissionRule(Base):
    __tablename__ = "permission_rules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        nullable=False,
    )
    resource_type: Mapped[ResourceType] = mapped_column(
        Enum(ResourceType, name="resource_type_enum"), nullable=False
    )
    resource_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    action: Mapped[PermissionAction] = mapped_column(
        Enum(PermissionAction, name="permission_action_enum"), nullable=False
    )
    effect: Mapped[PermissionEffect] = mapped_column(
        Enum(PermissionEffect, name="permission_effect_enum"),
        nullable=False,
        default=PermissionEffect.ALLOW,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )

    role: Mapped["Role"] = relationship("Role", back_populates="permission_rules")

    __table_args__ = (
        Index(
            "idx_perm_rules_eval",
            "role_id",
            "action",
            "effect",
            "resource_type",
            "resource_id",
        ),
    )


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="CASCADE"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )

    parent: Mapped[Optional["Category"]] = relationship(
        "Category",
        remote_side=[id],
        back_populates="children",
    )
    children: Mapped[List["Category"]] = relationship(
        "Category",
        back_populates="parent",
        cascade="all, delete-orphan",
    )
    assignments: Mapped[List["CategoryAssignment"]] = relationship(
        "CategoryAssignment", back_populates="category", cascade="all, delete-orphan"
    )


class CategoryAssignment(Base):
    __tablename__ = "category_assignments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="CASCADE"),
        nullable=False,
    )
    entity_type: Mapped[EntityType] = mapped_column(
        Enum(EntityType, name="entity_type_enum"), nullable=False
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )

    category: Mapped["Category"] = relationship(
        "Category", back_populates="assignments"
    )

    __table_args__ = (
        UniqueConstraint(
            "category_id", "entity_type", "entity_id", name="uq_cat_assign"
        ),
        Index("idx_cat_assign_lookup", "entity_type", "entity_id", "category_id"),
    )
