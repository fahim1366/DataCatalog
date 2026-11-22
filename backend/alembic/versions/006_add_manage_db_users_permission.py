"""Add MANAGE_DB_USERS to permission_action_enum

Revision ID: 006_add_manage_db_users_permission
Revises: 005_db_objects_indexes_routines
Create Date: 2026-11-22
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '006_add_manage_db_users_perm'
down_revision: Union[str, None] = '005_db_objects_indexes_routines'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a multi-statement transaction block in PostgreSQL
    op.execute("COMMIT")
    op.execute("ALTER TYPE permission_action_enum ADD VALUE IF NOT EXISTS 'MANAGE_DB_USERS'")
    op.execute("BEGIN")


def downgrade() -> None:
    # PostgreSQL ENUM values cannot be dropped with ALTER TYPE DROP VALUE
    pass
