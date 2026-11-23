"""Add collation fields to DatabaseConnection and CatalogColumn

Revision ID: 007_add_collation_fields
Revises: 006_add_manage_db_users_perm
Create Date: 2026-11-22
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '007_add_collation_fields'
down_revision: Union[str, None] = '006_add_manage_db_users_perm'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'database_connections',
        sa.Column('collation', sa.String(length=128), nullable=True, quote=True)
    )
    op.add_column(
        'catalog_columns',
        sa.Column('collation', sa.String(length=128), nullable=True, quote=True)
    )


def downgrade() -> None:
    op.execute('ALTER TABLE catalog_columns DROP COLUMN IF EXISTS "collation"')
    op.execute('ALTER TABLE database_connections DROP COLUMN IF EXISTS "collation"')
