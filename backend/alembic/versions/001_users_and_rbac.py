"""Database Schema & Alembic Migration for Users and RBAC

Revision ID: 001_users_and_rbac
Revises: 
Create Date: 2026-11-10 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "001_users_and_rbac"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. PostgreSQL Extensions
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')
    op.execute('CREATE EXTENSION IF NOT EXISTS "pg_trgm";')
    op.execute('CREATE EXTENSION IF NOT EXISTS "btree_gist";')

    # 2. ENUM Types with idempotent creation
    op.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'user_status_enum') THEN
            CREATE TYPE user_status_enum AS ENUM ('ACTIVE', 'SUSPENDED', 'DEACTIVATED');
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'permission_action_enum') THEN
            CREATE TYPE permission_action_enum AS ENUM (
                'VIEW_METADATA', 'EDIT_DESCRIPTION', 'MANAGE_TAGS',
                'MANAGE_CONNECTIONS', 'VIEW_AUDIT_LOGS', 'EXPORT_REPORTS'
            );
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'permission_effect_enum') THEN
            CREATE TYPE permission_effect_enum AS ENUM ('ALLOW', 'DENY');
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'resource_type_enum') THEN
            CREATE TYPE resource_type_enum AS ENUM (
                'GLOBAL', 'DATABASE', 'SCHEMA', 'TABLE', 'COLUMN', 'CATEGORY'
            );
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'entity_type_enum') THEN
            CREATE TYPE entity_type_enum AS ENUM ('DATABASE', 'SCHEMA', 'TABLE', 'COLUMN');
        END IF;
    END$$;
    """)

    # 3. Users Table
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("username", sa.String(100), nullable=False, unique=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column(
            "status",
            sa.Enum("ACTIVE", "SUSPENDED", "DEACTIVATED", name="user_status_enum", create_type=False),
            nullable=False,
            server_default="ACTIVE",
        ),
        sa.Column("is_superadmin", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("clock_timestamp()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("clock_timestamp()")),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # 4. Roles Table
    op.create_table(
        "roles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_system_role", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("clock_timestamp()")),
    )

    # 5. UserRoles Table
    op.create_table(
        "user_roles",
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role_id", UUID(as_uuid=True), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("clock_timestamp()")),
        sa.Column("assigned_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )

    # 6. PermissionRules Table
    op.create_table(
        "permission_rules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("role_id", UUID(as_uuid=True), sa.ForeignKey("roles.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "resource_type",
            sa.Enum("GLOBAL", "DATABASE", "SCHEMA", "TABLE", "COLUMN", "CATEGORY", name="resource_type_enum", create_type=False),
            nullable=False,
        ),
        sa.Column("resource_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "action",
            sa.Enum(
                "VIEW_METADATA", "EDIT_DESCRIPTION", "MANAGE_TAGS",
                "MANAGE_CONNECTIONS", "VIEW_AUDIT_LOGS", "EXPORT_REPORTS",
                name="permission_action_enum",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "effect",
            sa.Enum("ALLOW", "DENY", name="permission_effect_enum", create_type=False),
            nullable=False,
            server_default="ALLOW",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("clock_timestamp()")),
    )
    # Composite index for sub-100ms RBAC evaluation
    op.create_index(
        "idx_perm_rules_eval",
        "permission_rules",
        ["role_id", "action", "effect", "resource_type", "resource_id"],
    )

    # 7. Categories Table (Hierarchical Self-Referencing Tree)
    op.create_table(
        "categories",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("parent_id", UUID(as_uuid=True), sa.ForeignKey("categories.id", ondelete="CASCADE"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("clock_timestamp()")),
    )

    # 8. Category Assignments Table
    op.create_table(
        "category_assignments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("category_id", UUID(as_uuid=True), sa.ForeignKey("categories.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "entity_type",
            sa.Enum("DATABASE", "SCHEMA", "TABLE", "COLUMN", name="entity_type_enum", create_type=False),
            nullable=False,
        ),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("clock_timestamp()")),
        sa.UniqueConstraint("category_id", "entity_type", "entity_id", name="uq_cat_assign"),
    )
    op.create_index(
        "idx_cat_assign_lookup",
        "category_assignments",
        ["entity_type", "entity_id", "category_id"],
    )


def downgrade() -> None:
    op.drop_table("category_assignments")
    op.drop_table("categories")
    op.drop_table("permission_rules")
    op.drop_table("user_roles")
    op.drop_table("roles")
    op.drop_table("users")

    op.execute("DROP TYPE IF EXISTS entity_type_enum;")
    op.execute("DROP TYPE IF EXISTS resource_type_enum;")
    op.execute("DROP TYPE IF EXISTS permission_effect_enum;")
    op.execute("DROP TYPE IF EXISTS permission_action_enum;")
    op.execute("DROP TYPE IF EXISTS user_status_enum;")
