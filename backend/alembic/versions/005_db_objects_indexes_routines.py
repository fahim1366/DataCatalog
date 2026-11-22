"""Database objects indexes and routines catalog tables

Revision ID: 005_db_objects_indexes_routines
Revises: 004_phase2_sync_and_stats
Create Date: 2026-11-22
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = '005_db_objects_indexes_routines'
down_revision: Union[str, None] = '004_phase2_sync_and_stats'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'catalog_indexes',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('table_id', UUID(as_uuid=True), sa.ForeignKey('catalog_tables.id', ondelete='CASCADE'), nullable=False),
        sa.Column('schema_id', UUID(as_uuid=True), sa.ForeignKey('schemas.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('is_unique', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('is_primary', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('index_type', sa.String(50), nullable=False, server_default=sa.text("'BTREE'")),
        sa.Column('columns_list', JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column('definition', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('clock_timestamp()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('clock_timestamp()')),
        sa.UniqueConstraint('table_id', 'name', name='uq_table_index_name'),
    )
    op.create_index('idx_catalog_indexes_schema', 'catalog_indexes', ['schema_id'])
    op.create_index('idx_catalog_indexes_table', 'catalog_indexes', ['table_id'])

    op.create_table(
        'catalog_routines',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('schema_id', UUID(as_uuid=True), sa.ForeignKey('schemas.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('routine_type', sa.String(20), nullable=False, server_default=sa.text("'FUNCTION'")),
        sa.Column('language', sa.String(50), nullable=False, server_default=sa.text("'sql'")),
        sa.Column('arguments_signature', sa.Text(), nullable=True),
        sa.Column('return_type', sa.String(255), nullable=True),
        sa.Column('source_description', sa.Text(), nullable=True),
        sa.Column('approved_description', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('clock_timestamp()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('clock_timestamp()')),
        sa.UniqueConstraint('schema_id', 'name', 'routine_type', name='uq_schema_routine_name_type'),
    )
    op.create_index('idx_catalog_routines_schema', 'catalog_routines', ['schema_id'])


def downgrade() -> None:
    op.drop_table('catalog_routines')
    op.drop_table('catalog_indexes')
