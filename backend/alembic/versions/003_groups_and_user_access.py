"""Database Schema & Alembic Migration for Groups, Group Roles, User Groups, and Direct User Permissions

Revision ID: 003_groups_and_user_access
Revises: 002_baseline_schema
Create Date: 2026-11-14 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "003_groups_and_user_access"
down_revision: Union[str, None] = "002_baseline_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Alter users table to add phone and last_login_at
    op.add_column("users", sa.Column("phone", sa.String(50), nullable=True))
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))

    # 2. Create groups table
    op.create_table(
        "groups",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("clock_timestamp()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("clock_timestamp()")),
    )
    op.create_index("ix_groups_name", "groups", ["name"], unique=True)

    # 3. Create user_groups table (many-to-many between users and groups)
    op.create_table(
        "user_groups",
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("group_id", UUID(as_uuid=True), sa.ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("clock_timestamp()")),
        sa.Column("assigned_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_user_groups_user_id", "user_groups", ["user_id"])
    op.create_index("ix_user_groups_group_id", "user_groups", ["group_id"])

    # 4. Create group_roles table (roles granted to all members of a group)
    op.create_table(
        "group_roles",
        sa.Column("group_id", UUID(as_uuid=True), sa.ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role_id", UUID(as_uuid=True), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("clock_timestamp()")),
        sa.Column("assigned_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_group_roles_group_id", "group_roles", ["group_id"])
    op.create_index("ix_group_roles_role_id", "group_roles", ["role_id"])

    # 5. Alter permission_rules table to support direct User & Group permission assignments
    # Make role_id nullable
    op.alter_column("permission_rules", "role_id", nullable=True)

    # Add user_id and group_id
    op.add_column("permission_rules", sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True))
    op.add_column("permission_rules", sa.Column("group_id", UUID(as_uuid=True), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=True))

    # Add indexes for efficient evaluation of direct user rules and group rules
    op.create_index(
        "idx_perm_rules_user_eval",
        "permission_rules",
        ["user_id", "action", "effect", "resource_type", "resource_id"],
    )
    op.create_index(
        "idx_perm_rules_group_eval",
        "permission_rules",
        ["group_id", "action", "effect", "resource_type", "resource_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_perm_rules_group_eval", table_name="permission_rules")
    op.drop_index("idx_perm_rules_user_eval", table_name="permission_rules")
    op.drop_column("permission_rules", "group_id")
    op.drop_column("permission_rules", "user_id")
    op.alter_column("permission_rules", "role_id", nullable=False)

    op.drop_index("ix_group_roles_role_id", table_name="group_roles")
    op.drop_index("ix_group_roles_group_id", table_name="group_roles")
    op.drop_table("group_roles")

    op.drop_index("ix_user_groups_group_id", table_name="user_groups")
    op.drop_index("ix_user_groups_user_id", table_name="user_groups")
    op.drop_table("user_groups")

    op.drop_index("ix_groups_name", table_name="groups")
    op.drop_table("groups")

    op.drop_column("users", "last_login_at")
    op.drop_column("users", "phone")
