import uuid
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.database import async_session_factory
from backend.src.models import (
    CatalogColumn,
    CatalogTable,
    ClassificationLevel,
    DatabaseConnection,
    DatabaseEngine,
    DatabaseSchema,
    LifecycleStatus,
    SyncSchedule,
    TableType,
)


@pytest.mark.asyncio
async def test_sync_job_defaults_and_jsonb_initialization():
    """Verify SyncJob insertion with default values and JSONB error_trace_jsonb initialization."""
    from backend.src.models.sync_jobs import SyncJob

    async with async_session_factory() as session:
        conn = DatabaseConnection(
            name=f"test-conn-{uuid.uuid4().hex[:8]}",
            engine_type=DatabaseEngine.POSTGRESQL,
            host="localhost",
            port=5432,
            database_name="testdb",
            owner="admin@datacatalog.local",
            encrypted_dek=b"dek",
            encrypted_credentials=b"creds",
        )
        session.add(conn)
        await session.flush()

        job = SyncJob(
            connection_id=conn.id,
        )
        session.add(job)
        await session.flush()
        await session.refresh(job)

        assert job.status == "QUEUED"
        assert job.triggered_by == "SCHEDULE"
        assert job.tables_scanned == 0
        assert job.tables_created == 0
        assert job.tables_updated == 0
        assert job.tables_deleted == 0
        assert job.columns_scanned == 0
        assert job.changes_detected == 0
        assert job.error_trace_jsonb == {} or job.error_trace_jsonb is not None
        assert job.created_at is not None

        await session.rollback()


@pytest.mark.asyncio
async def test_cascade_delete_connection_removes_sync_jobs():
    """Verify cascade delete of DatabaseConnection removes associated sync_jobs."""
    from backend.src.models.sync_jobs import SyncJob

    async with async_session_factory() as session:
        conn = DatabaseConnection(
            name=f"test-conn-{uuid.uuid4().hex[:8]}",
            engine_type=DatabaseEngine.POSTGRESQL,
            host="localhost",
            port=5432,
            database_name="testdb",
            owner="admin@datacatalog.local",
            encrypted_dek=b"dek",
            encrypted_credentials=b"creds",
        )
        session.add(conn)
        await session.flush()

        job1 = SyncJob(connection_id=conn.id, status="SUCCESS")
        job2 = SyncJob(connection_id=conn.id, status="FAILED")
        session.add_all([job1, job2])
        await session.flush()

        job_ids = [job1.id, job2.id]

        # Delete the connection
        await session.delete(conn)
        await session.flush()

        # Query sync_jobs directly via raw SQL
        result = await session.execute(
            text("SELECT id FROM sync_jobs WHERE id = ANY(:ids)"),
            {"ids": job_ids},
        )
        remaining = result.fetchall()
        assert len(remaining) == 0, "sync_jobs were not cascade-deleted with connection"

        await session.rollback()


@pytest.mark.asyncio
async def test_cascade_delete_table_removes_statistics_history():
    """Verify cascade delete of CatalogTable removes associated table_statistics_history."""
    from backend.src.models.statistics import TableStatisticsHistory
    from backend.src.models.sync_jobs import SyncJob

    async with async_session_factory() as session:
        conn = DatabaseConnection(
            name=f"test-conn-{uuid.uuid4().hex[:8]}",
            engine_type=DatabaseEngine.POSTGRESQL,
            host="localhost",
            port=5432,
            database_name="testdb",
            owner="admin@datacatalog.local",
            encrypted_dek=b"dek",
            encrypted_credentials=b"creds",
        )
        session.add(conn)
        await session.flush()

        schema = DatabaseSchema(
            connection_id=conn.id,
            name=f"schema_{uuid.uuid4().hex[:8]}",
        )
        session.add(schema)
        await session.flush()

        table = CatalogTable(
            schema_id=schema.id,
            name=f"table_{uuid.uuid4().hex[:8]}",
            table_type=TableType.TABLE,
        )
        session.add(table)
        await session.flush()

        job = SyncJob(connection_id=conn.id)
        session.add(job)
        await session.flush()

        stat1 = TableStatisticsHistory(
            table_id=table.id,
            row_count_estimate=1000,
            size_bytes=50000,
            sync_job_id=job.id,
        )
        stat2 = TableStatisticsHistory(
            table_id=table.id,
            row_count_estimate=2000,
            size_bytes=100000,
        )
        session.add_all([stat1, stat2])
        await session.flush()

        stat_ids = [stat1.id, stat2.id]

        # Delete table
        await session.delete(table)
        await session.flush()

        # Query stats
        result = await session.execute(
            text("SELECT id FROM table_statistics_history WHERE id = ANY(:ids)"),
            {"ids": stat_ids},
        )
        remaining = result.fetchall()
        assert len(remaining) == 0, "table_statistics_history was not cascade-deleted with table"

        await session.rollback()


@pytest.mark.asyncio
async def test_explain_index_scan_sync_jobs_connection_started():
    """Verify index scan on idx_sync_jobs_connection_started via EXPLAIN."""
    test_conn_id = uuid.uuid4()
    async with async_session_factory() as session:
        # Run EXPLAIN on the connection + started_at query
        explain_query = text(
            "EXPLAIN SELECT id FROM sync_jobs WHERE connection_id = :conn_id ORDER BY started_at DESC"
        )
        result = await session.execute(explain_query, {"conn_id": test_conn_id})
        explain_lines = [row[0] for row in result.fetchall()]
        explain_text = " ".join(explain_lines).lower()

        # In PostgreSQL with an empty or populated table, planner should reference the index or index scan/bitmap index scan
        assert (
            "idx_sync_jobs_connection_started" in explain_text
            or "index scan" in explain_text
            or "bitmap" in explain_text
            or "seq scan" in explain_text  # On completely empty table, postgres may seq scan unless enable_seqscan=off
        )

        # Force index scan by disabling seq scan in session to prove index is valid and usable
        await session.execute(text("SET LOCAL enable_seqscan = off;"))
        result_force = await session.execute(explain_query, {"conn_id": test_conn_id})
        explain_force = " ".join([row[0] for row in result_force.fetchall()]).lower()
        assert "idx_sync_jobs_connection_started" in explain_force, (
            f"Expected idx_sync_jobs_connection_started in plan: {explain_force}"
        )


@pytest.mark.asyncio
async def test_circuit_breaker_and_drift_fields_persistence():
    """Verify circuit-breaker columns on SyncSchedule and description_drift_detected on CatalogTable/Column."""
    async with async_session_factory() as session:
        conn = DatabaseConnection(
            name=f"test-conn-{uuid.uuid4().hex[:8]}",
            engine_type=DatabaseEngine.POSTGRESQL,
            host="localhost",
            port=5432,
            database_name="testdb",
            owner="admin@datacatalog.local",
            encrypted_dek=b"dek",
            encrypted_credentials=b"creds",
        )
        session.add(conn)
        await session.flush()

        schedule = SyncSchedule(
            connection_id=conn.id,
            cron_expression="0 * * * *",
            circuit_state="OPEN",
            consecutive_failures=3,
        )
        session.add(schedule)
        await session.flush()
        await session.refresh(schedule)

        assert schedule.circuit_state == "OPEN"
        assert schedule.consecutive_failures == 3

        schema = DatabaseSchema(
            connection_id=conn.id,
            name=f"schema_{uuid.uuid4().hex[:8]}",
        )
        session.add(schema)
        await session.flush()

        table = CatalogTable(
            schema_id=schema.id,
            name=f"table_{uuid.uuid4().hex[:8]}",
            table_type=TableType.TABLE,
            description_drift_detected=True,
        )
        session.add(table)
        await session.flush()
        await session.refresh(table)

        assert table.description_drift_detected is True

        column = CatalogColumn(
            table_id=table.id,
            name="col1",
            ordinal_position=1,
            data_type="integer",
            raw_data_type="int4",
            description_drift_detected=True,
        )
        session.add(column)
        await session.flush()
        await session.refresh(column)

        assert column.description_drift_detected is True

        await session.rollback()
