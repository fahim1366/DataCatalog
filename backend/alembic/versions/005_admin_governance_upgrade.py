"""Admin governance upgrade: permissions, mongodb, curation, and user access constraints

Revision ID: 005_admin_governance_upgrade
Revises: 004_add_category_status
Create Date: 2026-11-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = '005_admin_governance_upgrade'
down_revision: Union[str, None] = '004_add_category_status'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Update ENUM types outside transaction block
    op.execute("COMMIT")
    op.execute("ALTER TYPE permission_action_enum ADD VALUE IF NOT EXISTS 'MANAGE_CATEGORIES'")
    op.execute("ALTER TYPE permission_action_enum ADD VALUE IF NOT EXISTS 'MANAGE_USERS'")
    op.execute("ALTER TYPE permission_action_enum ADD VALUE IF NOT EXISTS 'MANAGE_ROLES'")
    op.execute("ALTER TYPE permission_action_enum ADD VALUE IF NOT EXISTS 'MANAGE_SCHEDULES'")
    op.execute("ALTER TYPE database_engine_enum ADD VALUE IF NOT EXISTS 'MONGODB'")
    op.execute("ALTER TYPE entity_type_enum ADD VALUE IF NOT EXISTS 'USER'")
    op.execute("ALTER TYPE entity_type_enum ADD VALUE IF NOT EXISTS 'ROLE'")
    op.execute("ALTER TYPE entity_type_enum ADD VALUE IF NOT EXISTS 'CATEGORY'")
    op.execute("BEGIN")

    # 2. Add columns to catalog_tables
    op.add_column("catalog_tables", sa.Column("ai_suggested_description", sa.Text(), nullable=True))
    op.add_column("catalog_tables", sa.Column("description_source", sa.String(50), nullable=False, server_default="SOURCE_DB"))
    op.add_column("catalog_tables", sa.Column("curated_by_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    op.add_column("catalog_tables", sa.Column("curated_by_username", sa.String(100), nullable=True))
    op.add_column("catalog_tables", sa.Column("curated_at", sa.DateTime(timezone=True), nullable=True))

    # 3. Add columns to catalog_columns
    op.add_column("catalog_columns", sa.Column("ai_suggested_description", sa.Text(), nullable=True))
    op.add_column("catalog_columns", sa.Column("description_source", sa.String(50), nullable=False, server_default="SOURCE_DB"))
    op.add_column("catalog_columns", sa.Column("curated_by_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    op.add_column("catalog_columns", sa.Column("curated_by_username", sa.String(100), nullable=True))
    op.add_column("catalog_columns", sa.Column("curated_at", sa.DateTime(timezone=True), nullable=True))

    # 4. Check constraint on permission_rules
    op.create_check_constraint(
        "ck_perm_rules_principal",
        "permission_rules",
        "(role_id IS NOT NULL AND user_id IS NULL AND group_id IS NULL) OR "
        "(role_id IS NULL AND user_id IS NOT NULL AND group_id IS NULL) OR "
        "(role_id IS NULL AND user_id IS NULL AND group_id IS NOT NULL)"
    )


def downgrade() -> None:
    op.drop_constraint("ck_perm_rules_principal", "permission_rules", type_="check")

    op.drop_column("catalog_columns", "curated_at")
    op.drop_column("catalog_columns", "curated_by_username")
    op.drop_column("catalog_columns", "curated_by_id")
    op.drop_column("catalog_columns", "description_source")
    op.drop_column("catalog_columns", "ai_suggested_description")

    op.drop_column("catalog_tables", "curated_at")
    op.drop_column("catalog_tables", "curated_by_username")
    op.drop_column("catalog_tables", "curated_by_id")
    op.drop_column("catalog_tables", "description_source")
    op.drop_column("catalog_tables", "ai_suggested_description")
