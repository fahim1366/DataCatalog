"""add category status

Revision ID: 004_add_category_status
Revises: 003_groups_and_user_access
Create Date: 2026-11-14

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '004_add_category_status'
down_revision: Union[str, None] = '003_groups_and_user_access'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'categories',
        sa.Column('status', sa.String(50), nullable=False, server_default='ACTIVE')
    )


def downgrade() -> None:
    op.drop_column('categories', 'status')
