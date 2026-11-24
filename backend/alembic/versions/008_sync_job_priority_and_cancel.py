"""Add priority and CANCELLED status to sync_jobs

Revision ID: 008_sync_job_priority_and_cancel
Revises: 007_add_collation_fields
Create Date: 2026-11-24
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '008_sync_job_priority_and_cancel'
down_revision: Union[str, None] = '007_add_collation_fields'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'sync_jobs',
        sa.Column('priority', sa.Integer(), nullable=False, server_default=sa.text('0'))
    )


def downgrade() -> None:
    op.execute('ALTER TABLE sync_jobs DROP COLUMN IF EXISTS "priority"')
