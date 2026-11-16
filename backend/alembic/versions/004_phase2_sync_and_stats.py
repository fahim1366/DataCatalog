"""Phase 2 database schema: sync_jobs, table_statistics_history, circuit-breaker columns, and description drift.

Revision ID: 004_phase2_sync_and_stats
Revises: 006_perm_integrity_partitions
Create Date: 2026-11-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "004_phase2_sync_and_stats"
down_revision: Union[str, None] = "006_perm_integrity_partitions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Extend schema_change_type_enum with DESCRIPTION_DRIFT
    op.execute("ALTER TYPE schema_change_type_enum ADD VALUE IF NOT EXISTS 'DESCRIPTION_DRIFT'")

    # 2. Table: sync_jobs
    op.create_table(
        "sync_jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("database_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "schedule_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sync_schedules.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.String(length=50),
            nullable=False,
            server_default="QUEUED",
        ),
        sa.Column(
            "triggered_by",
            sa.String(length=50),
            nullable=False,
            server_default="SCHEDULE",
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("tables_scanned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tables_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tables_updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tables_deleted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("columns_scanned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("changes_detected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "error_trace_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
    )

    op.create_index(
        "idx_sync_jobs_connection_started",
        "sync_jobs",
        ["connection_id", sa.text("started_at DESC")],
    )
    op.create_index(
        "idx_sync_jobs_status",
        "sync_jobs",
        ["status"],
    )
    op.create_index(
        "idx_sync_jobs_created_at",
        "sync_jobs",
        [sa.text("created_at DESC")],
    )

    # 3. Table: table_statistics_history
    op.create_table(
        "table_statistics_history",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "table_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("catalog_tables.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("row_count_estimate", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "sync_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sync_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    op.create_index(
        "idx_table_stats_table_time",
        "table_statistics_history",
        ["table_id", sa.text("recorded_at DESC")],
    )
    op.create_index(
        "idx_table_stats_sync_job",
        "table_statistics_history",
        ["sync_job_id"],
    )

    # 4. Column additions: sync_schedules circuit breaker
    op.add_column(
        "sync_schedules",
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "sync_schedules",
        sa.Column("circuit_state", sa.String(length=20), nullable=False, server_default="CLOSED"),
    )
    op.add_column(
        "sync_schedules",
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
    )

    # 5. Column additions: description drift
    op.add_column(
        "catalog_tables",
        sa.Column("description_drift_detected", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "catalog_columns",
        sa.Column("description_drift_detected", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    # 5. Drop description drift columns
    op.drop_column("catalog_columns", "description_drift_detected")
    op.drop_column("catalog_tables", "description_drift_detected")

    # 4. Drop circuit breaker columns
    op.drop_column("sync_schedules", "cooldown_until")
    op.drop_column("sync_schedules", "circuit_state")
    op.drop_column("sync_schedules", "consecutive_failures")

    # 3. Drop table_statistics_history
    op.drop_index("idx_table_stats_sync_job", table_name="table_statistics_history")
    op.drop_index("idx_table_stats_table_time", table_name="table_statistics_history")
    op.drop_table("table_statistics_history")

    # 2. Drop sync_jobs
    op.drop_index("idx_sync_jobs_created_at", table_name="sync_jobs")
    op.drop_index("idx_sync_jobs_status", table_name="sync_jobs")
    op.drop_index("idx_sync_jobs_connection_started", table_name="sync_jobs")
    op.drop_table("sync_jobs")
