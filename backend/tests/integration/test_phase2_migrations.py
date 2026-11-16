import os
import pytest
from sqlalchemy import text
from alembic.config import Config
from alembic import command
from backend.src.core.database import engine


@pytest.mark.asyncio
async def test_phase2_tables_exist():
    """Verify that sync_jobs and table_statistics_history tables exist."""
    required_tables = {
        "sync_jobs",
        "table_statistics_history",
    }
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            )
        )
        existing_tables = {row[0] for row in result.fetchall()}
        missing = required_tables - existing_tables
        assert not missing, f"Missing Phase 2 tables: {missing}"


@pytest.mark.asyncio
async def test_sync_schedules_circuit_breaker_columns():
    """Verify circuit-breaker columns exist on sync_schedules."""
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT column_name, data_type, column_default, is_nullable "
                "FROM information_schema.columns WHERE table_name = 'sync_schedules'"
            )
        )
        cols = {row[0]: row for row in result.fetchall()}
        assert "consecutive_failures" in cols, "Missing consecutive_failures on sync_schedules"
        assert "circuit_state" in cols, "Missing circuit_state on sync_schedules"
        assert "cooldown_until" in cols, "Missing cooldown_until on sync_schedules"
        assert cols["circuit_state"][3] == "NO", "circuit_state should be NOT NULL"
        assert cols["consecutive_failures"][3] == "NO", "consecutive_failures should be NOT NULL"


@pytest.mark.asyncio
async def test_description_drift_detected_columns():
    """Verify description_drift_detected boolean columns exist on catalog_tables and catalog_columns."""
    async with engine.connect() as conn:
        for tbl in ["catalog_tables", "catalog_columns"]:
            result = await conn.execute(
                text(
                    f"SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                    f"WHERE table_name = '{tbl}' AND column_name = 'description_drift_detected'"
                )
            )
            row = result.fetchone()
            assert row is not None, f"Missing description_drift_detected on {tbl}"
            assert row[1] == "boolean", f"description_drift_detected should be boolean on {tbl}"
            assert row[2] == "NO", f"description_drift_detected should be NOT NULL on {tbl}"


@pytest.mark.asyncio
async def test_description_drift_enum_value():
    """Verify DESCRIPTION_DRIFT value exists in schema_change_type_enum."""
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT enumlabel FROM pg_enum "
                "JOIN pg_type ON pg_enum.enumtypid = pg_type.oid "
                "WHERE pg_type.typname = 'schema_change_type_enum'"
            )
        )
        enum_values = {row[0] for row in result.fetchall()}
        assert "DESCRIPTION_DRIFT" in enum_values, "DESCRIPTION_DRIFT missing from schema_change_type_enum"


@pytest.mark.asyncio
async def test_phase2_indexes_exist():
    """Verify Phase 2 composite and search indexes exist."""
    required_indexes = {
        "idx_sync_jobs_connection_started",
        "idx_sync_jobs_status",
        "idx_sync_jobs_created_at",
        "idx_table_stats_table_time",
        "idx_table_stats_sync_job",
    }
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT indexname FROM pg_indexes WHERE schemaname = 'public'"
            )
        )
        existing_indexes = {row[0] for row in result.fetchall()}
        missing = required_indexes - existing_indexes
        assert not missing, f"Missing Phase 2 indexes: {missing}"


def test_alembic_downgrade_and_upgrade_cycle():
    """Verify schema downgrade to 006_perm_integrity_partitions and re-upgrade to head."""
    ini_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../alembic.ini"))
    alembic_cfg = Config(ini_path)
    # Downgrade 1 revision back to 006_perm_integrity_partitions
    command.downgrade(alembic_cfg, "006_perm_integrity_partitions")
    # Upgrade back to head
    command.upgrade(alembic_cfg, "head")
