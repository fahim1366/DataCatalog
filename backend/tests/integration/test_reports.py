from __future__ import annotations

import asyncio
import time
import uuid
import pytest
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.database import async_session_factory, engine
from backend.src.models.catalog import (
    CatalogColumn,
    CatalogTable,
    ClassificationLevel,
    DatabaseSchema,
    LifecycleStatus,
    TableType,
)
from backend.src.models.connections import DatabaseConnection, DatabaseEngine
from backend.src.services.reporting.report_service import report_service


@pytest.fixture
async def reporting_test_data():
    """
    Sets up two isolated database connections with schemas, tables, and columns
    having varied descriptions, owners, and classification levels to rigorously
    validate PostgreSQL materialized view aggregations.
    Committed to database so materialized views can query them, then cleaned up in teardown.
    """
    suffix = uuid.uuid4().hex[:8]
    conn1_id = uuid.uuid4()
    conn2_id = uuid.uuid4()

    created_connection_ids = [conn1_id, conn2_id]

    async with async_session_factory() as session:
        # DB 1: Finance Warehouse
        conn1 = DatabaseConnection(
            id=conn1_id,
            name=f"finance_db_{suffix}",
            engine_type=DatabaseEngine.POSTGRESQL,
            host="localhost",
            port=5432,
            database_name=f"finance_db_{suffix}",
            owner="finance_lead",
            kek_version=1,
            encrypted_dek=b"dummy_dek_32_bytes_finance_rep",
            encrypted_credentials=b"dummy_creds_finance_report",
        )
        session.add(conn1)

        # DB 2: Analytics Lakehouse
        conn2 = DatabaseConnection(
            id=conn2_id,
            name=f"analytics_db_{suffix}",
            engine_type=DatabaseEngine.POSTGRESQL,
            host="localhost",
            port=5432,
            database_name=f"analytics_db_{suffix}",
            owner="analytics_lead",
            kek_version=1,
            encrypted_dek=b"dummy_dek_32_bytes_analyt_rep",
            encrypted_credentials=b"dummy_creds_analyt_report",
        )
        session.add(conn2)
        await session.flush()

        # Schemas for DB 1
        s1 = DatabaseSchema(
            id=uuid.uuid4(),
            connection_id=conn1_id,
            name="core",
            status=LifecycleStatus.ACTIVE,
        )
        s2 = DatabaseSchema(
            id=uuid.uuid4(),
            connection_id=conn1_id,
            name="audit",
            status=LifecycleStatus.ACTIVE,
        )
        # Deleted schema in DB 1 (must be excluded)
        s_del = DatabaseSchema(
            id=uuid.uuid4(),
            connection_id=conn1_id,
            name="deprecated_schema",
            status=LifecycleStatus.DELETED,
        )
        session.add_all([s1, s2, s_del])
        await session.flush()

        # Tables for Schema 1 (DB 1)
        # Table 1: with source_description and owner
        t1 = CatalogTable(
            id=uuid.uuid4(),
            schema_id=s1.id,
            name="invoices",
            table_type=TableType.TABLE,
            source_description="Customer invoices",
            owner="billing_team",
            status=LifecycleStatus.ACTIVE,
        )
        # Table 2: without description and without owner
        t2 = CatalogTable(
            id=uuid.uuid4(),
            schema_id=s1.id,
            name="invoice_items",
            table_type=TableType.TABLE,
            source_description=None,
            approved_description=None,
            owner=None,
            status=LifecycleStatus.ACTIVE,
        )
        # Tables for Schema 2 (DB 1)
        # Table 3: with approved_description and owner
        t3 = CatalogTable(
            id=uuid.uuid4(),
            schema_id=s2.id,
            name="audit_events",
            table_type=TableType.TABLE,
            approved_description="Audited events log",
            owner="security_team",
            status=LifecycleStatus.ACTIVE,
        )
        # Deleted table in Schema 1 (must be excluded)
        t_del = CatalogTable(
            id=uuid.uuid4(),
            schema_id=s1.id,
            name="deleted_table",
            table_type=TableType.TABLE,
            status=LifecycleStatus.DELETED,
        )
        session.add_all([t1, t2, t3, t_del])
        await session.flush()

        # Columns for Table 1 (DB 1) - 3 columns (PII, CONFIDENTIAL, INTERNAL)
        c1 = CatalogColumn(
            id=uuid.uuid4(),
            table_id=t1.id,
            name="customer_ssn",
            ordinal_position=1,
            data_type="varchar",
            raw_data_type="varchar(32)",
            classification_level=ClassificationLevel.PII,
            status=LifecycleStatus.ACTIVE,
        )
        c2 = CatalogColumn(
            id=uuid.uuid4(),
            table_id=t1.id,
            name="amount",
            ordinal_position=2,
            data_type="numeric",
            raw_data_type="numeric(12,2)",
            classification_level=ClassificationLevel.CONFIDENTIAL,
            status=LifecycleStatus.ACTIVE,
        )
        c3 = CatalogColumn(
            id=uuid.uuid4(),
            table_id=t1.id,
            name="created_at",
            ordinal_position=3,
            data_type="timestamptz",
            raw_data_type="timestamptz",
            classification_level=ClassificationLevel.INTERNAL,
            status=LifecycleStatus.ACTIVE,
        )
        # Deleted column in Table 1 (must be excluded)
        c_del = CatalogColumn(
            id=uuid.uuid4(),
            table_id=t1.id,
            name="deprecated_col",
            ordinal_position=4,
            data_type="varchar",
            raw_data_type="varchar(50)",
            classification_level=ClassificationLevel.PII,
            status=LifecycleStatus.DELETED,
        )
        # Columns for Table 2 (DB 1) - 2 columns (PUBLIC)
        c4 = CatalogColumn(
            id=uuid.uuid4(),
            table_id=t2.id,
            name="item_id",
            ordinal_position=1,
            data_type="uuid",
            raw_data_type="uuid",
            classification_level=ClassificationLevel.PUBLIC,
            status=LifecycleStatus.ACTIVE,
        )
        c5 = CatalogColumn(
            id=uuid.uuid4(),
            table_id=t2.id,
            name="quantity",
            ordinal_position=2,
            data_type="int",
            raw_data_type="int",
            classification_level=ClassificationLevel.PUBLIC,
            status=LifecycleStatus.ACTIVE,
        )
        # Columns for Table 3 (DB 1) - 2 columns (PII)
        c6 = CatalogColumn(
            id=uuid.uuid4(),
            table_id=t3.id,
            name="user_email",
            ordinal_position=1,
            data_type="varchar",
            raw_data_type="varchar(255)",
            classification_level=ClassificationLevel.PII,
            status=LifecycleStatus.ACTIVE,
        )
        c7 = CatalogColumn(
            id=uuid.uuid4(),
            table_id=t3.id,
            name="ip_address",
            ordinal_position=2,
            data_type="varchar",
            raw_data_type="inet",
            classification_level=ClassificationLevel.PII,
            status=LifecycleStatus.ACTIVE,
        )
        session.add_all([c1, c2, c3, c_del, c4, c5, c6, c7])

        # DB 2: 1 schema, 1 table with description and owner, 1 column (INTERNAL)
        s_db2 = DatabaseSchema(
            id=uuid.uuid4(),
            connection_id=conn2_id,
            name="metrics",
            status=LifecycleStatus.ACTIVE,
        )
        session.add(s_db2)
        await session.flush()

        t_db2 = CatalogTable(
            id=uuid.uuid4(),
            schema_id=s_db2.id,
            name="kpi_daily",
            table_type=TableType.TABLE,
            source_description="Daily enterprise metrics",
            owner="analytics_lead",
            status=LifecycleStatus.ACTIVE,
        )
        session.add(t_db2)
        await session.flush()

        c_db2 = CatalogColumn(
            id=uuid.uuid4(),
            table_id=t_db2.id,
            name="metric_value",
            ordinal_position=1,
            data_type="double precision",
            raw_data_type="float8",
            classification_level=ClassificationLevel.INTERNAL,
            status=LifecycleStatus.ACTIVE,
        )
        session.add(c_db2)
        await session.commit()

    # Prime views with the committed test dataset
    await report_service.refresh_all_materialized_views_concurrently()

    try:
        yield {
            "conn1_id": conn1_id,
            "conn2_id": conn2_id,
            "conn1_name": f"finance_db_{suffix}",
            "conn2_name": f"analytics_db_{suffix}",
            "t1_id": t1.id,
            "t2_id": t2.id,
            "t3_id": t3.id,
            "t_db2_id": t_db2.id,
        }
    finally:
        async with async_session_factory() as cleanup_session:
            for cid in created_connection_ids:
                conn_obj = await cleanup_session.get(DatabaseConnection, cid)
                if conn_obj:
                    await cleanup_session.delete(conn_obj)
            await cleanup_session.commit()

        # Refresh views to remove test artifacts
        await report_service.refresh_all_materialized_views_concurrently()


@pytest.mark.asyncio
async def test_materialized_views_exact_counts_and_percentages(reporting_test_data):
    """
    T040 Step A: Assert that mv_metadata_completeness and mv_classification_distribution
    compute exact counts, percentages, and filter out DELETED items correctly.
    """
    # 1. Concurrently refresh materialized views
    refresh_result = await report_service.refresh_all_materialized_views_concurrently()
    assert refresh_result["mv_metadata_completeness"] == "REFRESHED"
    assert refresh_result["mv_classification_distribution"] == "REFRESHED"

    # 2. Query mv_metadata_completeness directly
    async with async_session_factory() as session:
        query_start = time.perf_counter()
        stmt = text("""
            SELECT 
                connection_id, database_name, schema_count, table_count, 
                column_count, tables_with_desc, tables_with_owner, table_desc_completeness_pct
            FROM mv_metadata_completeness
            WHERE connection_id IN (:conn1, :conn2)
            ORDER BY database_name;
        """)
        result = await session.execute(
            stmt,
            {"conn1": reporting_test_data["conn1_id"], "conn2": reporting_test_data["conn2_id"]},
        )
        rows = {r.connection_id: r for r in result.fetchall()}
        query_latency = (time.perf_counter() - query_start) * 1000

        # Sub-50ms performance requirement verification
        assert query_latency < 50.0, f"Materialized view query latency was {query_latency:.2f}ms (expected < 50ms)"

        # Assert DB 1 metrics
        conn1_metrics = rows.get(reporting_test_data["conn1_id"])
        assert conn1_metrics is not None
        assert conn1_metrics.schema_count == 2
        assert conn1_metrics.table_count == 3  # t1, t2, t3 (excluding deleted t_del)
        assert conn1_metrics.column_count == 7  # 3 in t1, 2 in t2, 2 in t3 (excluding deleted c_del)
        assert conn1_metrics.tables_with_desc == 2  # t1 (source_description) + t3 (approved_description)
        assert conn1_metrics.tables_with_owner == 2  # t1 (billing_team) + t3 (security_team)
        # Completeness: 2 / 3 * 100 = 66.67%
        assert float(conn1_metrics.table_desc_completeness_pct) == 66.67

        # Assert DB 2 metrics
        conn2_metrics = rows.get(reporting_test_data["conn2_id"])
        assert conn2_metrics is not None
        assert conn2_metrics.schema_count == 1
        assert conn2_metrics.table_count == 1
        assert conn2_metrics.column_count == 1
        assert conn2_metrics.tables_with_desc == 1
        assert conn2_metrics.tables_with_owner == 1
        assert float(conn2_metrics.table_desc_completeness_pct) == 100.00


@pytest.mark.asyncio
async def test_materialized_views_classification_distribution(reporting_test_data):
    """
    T040 Step A: Assert that mv_classification_distribution computes exact column counts
    and affected table counts per classification level.
    """
    await report_service.refresh_all_materialized_views_concurrently()

    async with async_session_factory() as session:
        query_start = time.perf_counter()
        stmt = text("""
            SELECT classification_level, column_count, affected_table_count
            FROM mv_classification_distribution
            ORDER BY classification_level;
        """)
        result = await session.execute(stmt)
        dist = {r.classification_level: r for r in result.fetchall()}
        query_latency = (time.perf_counter() - query_start) * 1000

        assert query_latency < 50.0, f"Classification view latency was {query_latency:.2f}ms (expected < 50ms)"

        # Verify PII metrics: at least 3 columns across 2 tables from our test dataset
        assert "PII" in dist
        assert dist["PII"].column_count >= 3
        assert dist["PII"].affected_table_count >= 2

        # Verify CONFIDENTIAL metrics: at least 1 column
        assert "CONFIDENTIAL" in dist
        assert dist["CONFIDENTIAL"].column_count >= 1

        # Verify INTERNAL metrics: at least 2 columns
        assert "INTERNAL" in dist
        assert dist["INTERNAL"].column_count >= 2

        # Verify PUBLIC metrics: at least 2 columns
        assert "PUBLIC" in dist
        assert dist["PUBLIC"].column_count >= 2


@pytest.mark.asyncio
async def test_concurrent_refresh_without_blocking_reads(reporting_test_data):
    """
    T040 Step A: Assert that REFRESH MATERIALIZED VIEW CONCURRENTLY completes successfully
    and concurrent reads execute simultaneously without blocking or transaction deadlocks.
    """
    async def concurrent_reader(reader_id: int):
        async with async_session_factory() as session:
            stmt = text("SELECT COUNT(*) FROM mv_metadata_completeness;")
            res = await session.execute(stmt)
            count = res.scalar_one()
            assert count >= 2
            return count

    # Run refresh and concurrent readers in parallel
    refresh_task = asyncio.create_task(report_service.refresh_all_materialized_views_concurrently())
    read_tasks = [asyncio.create_task(concurrent_reader(i)) for i in range(5)]

    results = await asyncio.gather(refresh_task, *read_tasks)
    refresh_res = results[0]
    read_counts = results[1:]

    assert refresh_res["mv_metadata_completeness"] == "REFRESHED"
    assert refresh_res["mv_classification_distribution"] == "REFRESHED"
    assert len(read_counts) == 5
    for cnt in read_counts:
        assert cnt >= 2


@pytest.mark.asyncio
async def test_report_service_query_aggregation(reporting_test_data):
    """
    T040 Step A: Assert ReportService returns the complete aggregated domain models
    with query latency under 50ms.
    """
    await report_service.refresh_all_materialized_views_concurrently()

    async with async_session_factory() as session:
        # 1. Metadata completeness report
        start_t = time.perf_counter()
        comp_report = await report_service.get_metadata_completeness(session)
        comp_latency = (time.perf_counter() - start_t) * 1000

        assert comp_latency < 50.0, f"get_metadata_completeness took {comp_latency:.2f}ms (expected < 50ms)"
        assert comp_report.total_databases >= 2
        assert comp_report.total_tables >= 4
        assert comp_report.total_columns >= 8
        assert 0.0 <= comp_report.overall_completeness_pct <= 100.0

        matched_dbs = [
            b for b in comp_report.database_breakdown
            if b.connection_id in (reporting_test_data["conn1_id"], reporting_test_data["conn2_id"])
        ]
        assert len(matched_dbs) == 2

        # 2. Classification report
        start_t2 = time.perf_counter()
        class_report = await report_service.get_classification_distribution(session)
        class_latency = (time.perf_counter() - start_t2) * 1000

        assert class_latency < 50.0, f"get_classification_distribution took {class_latency:.2f}ms (expected < 50ms)"
        assert class_report.total_classified_columns >= 8
        assert len(class_report.breakdown) >= 4
