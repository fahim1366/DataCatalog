"""Add referenced_table and referenced_column to catalog_columns

Revision ID: 009_add_fk_target_columns
Revises: 008_sync_job_priority_and_cancel
Create Date: 2026-11-28
"""
from typing import Sequence, Union
from alembic import op

revision: str = '009_add_fk_target_columns'
down_revision: Union[str, None] = '008_sync_job_priority_and_cancel'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute('ALTER TABLE catalog_columns ADD COLUMN IF NOT EXISTS referenced_table VARCHAR(255)')
    op.execute('ALTER TABLE catalog_columns ADD COLUMN IF NOT EXISTS referenced_column VARCHAR(255)')


def downgrade() -> None:
    op.execute('ALTER TABLE catalog_columns DROP COLUMN IF EXISTS referenced_column')
    op.execute('ALTER TABLE catalog_columns DROP COLUMN IF EXISTS referenced_table')
