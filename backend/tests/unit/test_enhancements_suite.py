from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest
from fastapi import HTTPException

from backend.src.api.v1.connections import batch_update_schedules
from backend.src.api.v1.sync_jobs import list_sync_jobs
from backend.src.models.connections import DatabaseConnection, DatabaseEngine, SyncSchedule
from backend.src.models.sync_jobs import SyncJob, SyncJobStatus, SyncTriggerType
from backend.src.schemas.connections import BatchScheduleRequest
from backend.src.services.connectors.base import (
    ColumnMetadata,
    ForeignKeyMetadata,
    SchemaExtractionResult,
    SchemaMetadata,
    TableMetadata,
)
from backend.src.services.connectors.clickhouse import ClickHouseConnector
from backend.src.services.connectors.mongodb import MongoDBConnector
from backend.src.services.connectors.mssql import MSSQLConnector, _detect_mssql_driver
from backend.src.services.connectors.mysql import MySQLConnector
from backend.src.services.connectors.oracle import OracleConnector
from backend.src.services.connectors.postgres import PostgresConnector
from backend.src.services.connectors.registry import default_connector_registry
from backend.src.services.workers.tasks import execute_metadata_sync


# ---------------------------------------------------------------------------
# 1. All 6 Engine Connectors: Registry, test_connection, discover_databases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "engine,expected_cls",
    [
        (DatabaseEngine.POSTGRESQL, PostgresConnector),
        (DatabaseEngine.MSSQL, MSSQLConnector),
        (DatabaseEngine.MYSQL, MySQLConnector),
        (DatabaseEngine.ORACLE, OracleConnector),
        (DatabaseEngine.CLICKHOUSE, ClickHouseConnector),
        (DatabaseEngine.MONGODB, MongoDBConnector),
    ],
)
def test_all_six_engines_registered(engine, expected_cls):
    connector = default_connector_registry.create_connector(
        engine, {"host": "127.0.0.1", "port": 1234, "database_name": "testdb"}
    )
    assert isinstance(connector, expected_cls)


@pytest.mark.asyncio
async def test_mssql_pymssql_fallback_without_odbc_driver():
    """MSSQLConnector falls back to pymssql when no SQL Server ODBC driver is present."""
    fake_pymssql = MagicMock()
    fake_conn = MagicMock()
    fake_cursor = MagicMock()
    fake_cursor.fetchall.return_value = [{"server_version": "Microsoft SQL Server 2022", "current_database": "sales"}]
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor
    fake_conn.cursor.return_value.__exit__.return_value = False
    fake_pymssql.connect.return_value = fake_conn

    with patch("backend.src.services.connectors.mssql._has_sql_server_odbc_driver", return_value=False), \
         patch.dict(sys.modules, {"pymssql": fake_pymssql}):
        assert _detect_mssql_driver() == "pymssql"
        connector = MSSQLConnector({"host": "10.0.0.10", "port": 1433, "database_name": "sales", "username": "sa", "password": "pw"})
        assert connector._uses_pymssql() is True

        res = await connector.test_connection()
        assert res.success is True
        assert res.dialect == "MSSQL"
        assert "2022" in (res.server_version or "")
        fake_pymssql.connect.assert_called_once()


@pytest.mark.asyncio
async def test_all_six_connectors_discover_databases_and_test():
    """Verify discover_databases_from_config and test_connection across all 6 engines."""
    # PostgreSQL
    pg_conn = PostgresConnector({"host": "127.0.0.1", "port": 5432, "database_name": "pg"})
    with patch.object(pg_conn, "_run_connection_test", new=AsyncMock(return_value=MagicMock(success=True, dialect="POSTGRESQL", server_version="PG 16"))):
        pg_res = await pg_conn.test_connection()
        assert pg_res.success is True

    # ClickHouse
    ch_conn = ClickHouseConnector({"host": "127.0.0.1", "port": 8123, "database_name": "default"})
    with patch.object(ch_conn, "_query_json", new=AsyncMock(side_effect=[
        [{"ver": "24.1", "current_db": "default"}],
        [{"name": "analytics"}],
        [{"ver": "24.1", "current_db": "default"}],
    ])):
        ch_res = await ch_conn.test_connection()
        assert ch_res.success is True
        assert ch_res.dialect == "CLICKHOUSE"

    # MySQL, Oracle, MongoDB discovery via classmethod
    for cls, engine in [
        (PostgresConnector, "POSTGRESQL"),
        (MSSQLConnector, "MSSQL"),
        (MySQLConnector, "MYSQL"),
        (OracleConnector, "ORACLE"),
        (ClickHouseConnector, "CLICKHOUSE"),
        (MongoDBConnector, "MONGODB"),
    ]:
        with patch.object(cls, "_run_database_discovery", new=AsyncMock(return_value={"success": True, "databases": ["db1"], "latency_ms": 5.0, "server_version": "1.0", "message": "ok"})):
            disc = await cls.discover_databases_from_config({"host": "127.0.0.1", "port": 1234})
            assert disc["success"] is True, f"Discovery failed for {engine}"
            assert disc["databases"] == ["db1"]


# ---------------------------------------------------------------------------
# 2. execute_metadata_sync: RUNNING transition, BaseException -> FAILED, FK mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_metadata_sync_marks_failed_on_base_exception():
    conn_id = uuid.uuid4()
    job_id = uuid.uuid4()
    mock_conn = SimpleNamespace(
        id=conn_id,
        engine_type=DatabaseEngine.MSSQL,
        host="10.0.0.5",
        port=1433,
        database_name="sales",
        custom_params={},
        encrypted_dek=b"k" * 32,
        encrypted_credentials=b"c" * 64,
        collation=None,
    )
    mock_job = SimpleNamespace(
        id=job_id,
        connection_id=conn_id,
        connection=mock_conn,
        schedule=None,
        status=SyncJobStatus.QUEUED.value,
        started_at=None,
        completed_at=None,
        duration_ms=None,
        error_message=None,
        error_trace_jsonb=None,
    )

    mock_session = AsyncMock()
    mock_res_initial = MagicMock()
    mock_res_initial.unique.return_value.scalar_one_or_none.return_value = mock_job
    mock_res_re = MagicMock()
    mock_res_re.scalar_one_or_none.return_value = mock_job
    mock_session.execute = AsyncMock(side_effect=[mock_res_initial, mock_res_re])

    with patch("backend.src.services.workers.tasks.EnvelopeEncryptionService") as mock_crypto, \
         patch("backend.src.services.workers.tasks.default_connector_registry") as mock_reg, \
         patch("backend.src.services.workers.tasks.circuit_breaker_manager") as mock_cb:
        mock_crypto.return_value.decrypt_credentials.return_value = {"username": "sa", "password": "secret"}
        mock_reg.create_connector.side_effect = asyncio.CancelledError("Worker cancelled mid-sync: mongodb://u:p@host")
        mock_cb.record_job_outcome = AsyncMock()

        res = await execute_metadata_sync(conn_id, job_id, session=mock_session)

    assert res["status"] == SyncJobStatus.FAILED.value
    assert mock_job.status == SyncJobStatus.FAILED.value
    assert mock_job.completed_at is not None
    assert "p" not in (mock_job.error_message or "") or "***" in (mock_job.error_message or "")


@pytest.mark.asyncio
async def test_execute_metadata_sync_populates_fk_targets_in_ingestion():
    conn_id = uuid.uuid4()
    job_id = uuid.uuid4()
    mock_conn = SimpleNamespace(
        id=conn_id,
        engine_type=DatabaseEngine.POSTGRESQL,
        host="127.0.0.1",
        port=5432,
        database_name="app_db",
        custom_params={},
        encrypted_dek=b"k" * 32,
        encrypted_credentials=b"c" * 64,
        collation="en_US.UTF-8",
    )
    mock_job = SimpleNamespace(
        id=job_id,
        connection_id=conn_id,
        connection=mock_conn,
        schedule=None,
        status=SyncJobStatus.QUEUED.value,
        started_at=None,
        completed_at=None,
        duration_ms=None,
        error_message=None,
        error_trace_jsonb=None,
        tables_scanned=0,
        columns_scanned=0,
        tables_created=0,
        tables_deleted=0,
        changes_detected=0,
    )

    extraction = SchemaExtractionResult(
        schema=SchemaMetadata(name="public"),
        tables=[TableMetadata(schema_name="public", name="orders", table_type="TABLE")],
        columns=[
            ColumnMetadata(
                schema_name="public",
                table_name="orders",
                name="user_id",
                ordinal_position=1,
                data_type="uuid",
                raw_data_type="uuid",
                is_foreign_key=True,
            )
        ],
        foreign_keys=[
            ForeignKeyMetadata(
                schema_name="public",
                table_name="orders",
                column_name="user_id",
                foreign_schema_name="public",
                foreign_table_name="users",
                foreign_column_name="id",
                constraint_name="fk_orders_user_id",
            )
        ],
        success=True,
    )

    mock_connector = AsyncMock()
    mock_connector.__aenter__ = AsyncMock(return_value=mock_connector)
    mock_connector.__aexit__ = AsyncMock(return_value=None)
    mock_connector.extract_database_collation = AsyncMock(return_value="en_US.UTF-8")
    mock_connector.extract_all_schemas = AsyncMock(return_value=[extraction])

    mock_session = AsyncMock()
    mock_res_initial = MagicMock()
    mock_res_initial.unique.return_value.scalar_one_or_none.return_value = mock_job
    mock_session.execute = AsyncMock(return_value=mock_res_initial)

    captured_payloads = []

    async def _fake_ingest(session, payload, sync_job_id, **kwargs):
        captured_payloads.append(payload)
        return SimpleNamespace(
            tables_upserted=1,
            tables_deleted=0,
            columns_upserted=1,
            columns_deleted=0,
            versions_created=0,
        )

    with patch("backend.src.services.workers.tasks.EnvelopeEncryptionService") as mock_crypto, \
         patch("backend.src.services.workers.tasks.default_connector_registry") as mock_reg, \
         patch("backend.src.services.workers.tasks.bulk_ingestion_service") as mock_ingest, \
         patch("backend.src.services.workers.tasks.circuit_breaker_manager") as mock_cb:
        mock_crypto.return_value.decrypt_credentials.return_value = {"username": "pg", "password": "pw"}
        mock_reg.create_connector.return_value = mock_connector
        mock_ingest.ingest_bulk_metadata = AsyncMock(side_effect=_fake_ingest)
        mock_cb.record_job_outcome = AsyncMock()

        res = await execute_metadata_sync(conn_id, job_id, session=mock_session)

    assert res["status"] == SyncJobStatus.SUCCESS.value
    assert len(captured_payloads) == 1
    col_payload = captured_payloads[0].tables[0].columns[0]
    assert col_payload.is_foreign_key is True
    assert col_payload.referenced_table == "users"
    assert col_payload.referenced_column == "id"


# ---------------------------------------------------------------------------
# 3. GET /api/v1/sync-jobs returns jobs across all engines
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_sync_jobs_returns_all_jobs_across_dialects():
    now = datetime.now(timezone.utc)
    jobs = [
        SyncJob(
            id=uuid.uuid4(),
            connection_id=uuid.uuid4(),
            status=SyncJobStatus.SUCCESS.value,
            triggered_by=SyncTriggerType.MANUAL.value,
            tables_scanned=5,
            tables_created=5,
            tables_updated=0,
            tables_deleted=0,
            columns_scanned=20,
            changes_detected=0,
            priority=10,
            created_at=now,
        )
        for _ in range(6)
    ]

    mock_db = AsyncMock()
    count_res = MagicMock()
    count_res.scalar.return_value = 6
    rows_res = MagicMock()
    rows_res.scalars.return_value.all.return_value = jobs
    mock_db.execute = AsyncMock(side_effect=[count_res, rows_res])

    res = await list_sync_jobs(
        status_filter=None,
        connection_id=None,
        cursor=None,
        limit=50,
        user_ctx=MagicMock(),
        db=mock_db,
    )
    assert res.total == 6
    assert len(res.items) == 6


# ---------------------------------------------------------------------------
# 4. POST /api/v1/connections/batch-schedule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_update_schedules_creates_and_updates_atomically():
    cid1, cid2 = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(timezone.utc)
    conn1 = DatabaseConnection(id=cid1, name="db1", engine_type=DatabaseEngine.POSTGRESQL, host="h1", port=5432, database_name="d1", encrypted_dek=b"k", encrypted_credentials=b"c")
    conn2 = DatabaseConnection(id=cid2, name="db2", engine_type=DatabaseEngine.MSSQL, host="h2", port=1433, database_name="d2", encrypted_dek=b"k", encrypted_credentials=b"c")
    existing_sched = SyncSchedule(
        id=uuid.uuid4(),
        connection_id=cid1,
        cron_expression="0 1 * * *",
        is_enabled=False,
        circuit_state="CLOSED",
        consecutive_failures=0,
        created_at=now,
        updated_at=now,
    )

    mock_db = AsyncMock()
    conns_res = MagicMock()
    conns_res.scalars.return_value.all.return_value = [conn1, conn2]
    scheds_res = MagicMock()
    scheds_res.scalars.return_value.all.return_value = [existing_sched]
    mock_db.execute = AsyncMock(side_effect=[conns_res, scheds_res])
    mock_db.add = MagicMock()

    async def _fake_refresh(obj):
        if getattr(obj, "created_at", None) is None:
            obj.created_at = now
        if getattr(obj, "updated_at", None) is None:
            obj.updated_at = now

    mock_db.refresh = AsyncMock(side_effect=_fake_refresh)

    user_ctx = SimpleNamespace(user=SimpleNamespace(username="admin"), user_id=uuid.uuid4())
    with patch("backend.src.api.v1.connections.record_audit_event", new=AsyncMock()) as mock_audit:
        res = await batch_update_schedules(
            payload=BatchScheduleRequest(
                connection_ids=[cid1, cid2],
                cron_expression="0 */6 * * *",
                is_enabled=True,
            ),
            user_ctx=user_ctx,
            db=mock_db,
        )

    assert res.updated_count == 2
    assert res.cron_expression == "0 */6 * * *"
    assert res.is_enabled is True
    assert existing_sched.cron_expression == "0 */6 * * *"
    assert existing_sched.is_enabled is True
    mock_db.add.assert_called_once()
    mock_audit.assert_awaited_once()
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_batch_update_schedules_rejects_invalid_cron():
    user_ctx = SimpleNamespace(user=SimpleNamespace(username="admin"), user_id=uuid.uuid4())
    with pytest.raises(HTTPException) as exc_info:
        await batch_update_schedules(
            payload=BatchScheduleRequest(
                connection_ids=[uuid.uuid4()],
                cron_expression="invalid cron",
                is_enabled=True,
            ),
            user_ctx=user_ctx,
            db=AsyncMock(),
        )
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_get_table_details_includes_fk_target_columns():
    from backend.src.models.catalog import ClassificationLevel, LifecycleStatus, TableType
    from backend.src.schemas.catalog import TableDetail
    from backend.src.services.catalog.search_service import search_service

    tbl_id = uuid.uuid4()
    schema_id = uuid.uuid4()
    col_id = uuid.uuid4()

    mock_col = SimpleNamespace(
        id=col_id,
        table_id=tbl_id,
        name="user_id",
        ordinal_position=1,
        data_type="uuid",
        raw_data_type="uuid",
        collation=None,
        is_nullable=False,
        is_primary_key=False,
        is_foreign_key=True,
        referenced_table="users",
        referenced_column="id",
        default_value=None,
        source_description=None,
        approved_description=None,
        effective_description=None,
        ai_suggested_description=None,
        description_source="SOURCE_DB",
        curated_by_id=None,
        curated_by_username=None,
        curated_at=None,
        classification_level=ClassificationLevel.INTERNAL,
        status=LifecycleStatus.ACTIVE,
    )
    mock_tbl = SimpleNamespace(
        id=tbl_id,
        schema_id=schema_id,
        name="orders",
        table_type=TableType.TABLE,
        row_count_estimate=10,
        size_bytes=1024,
        source_description=None,
        approved_description=None,
        effective_description=None,
        ai_suggested_description=None,
        description_source="SOURCE_DB",
        curated_by_id=None,
        curated_by_username=None,
        curated_at=None,
        owner="postgres",
        status=LifecycleStatus.ACTIVE,
        custom_properties={},
        columns=[mock_col],
    )

    mock_session = AsyncMock()
    tbl_res = MagicMock()
    tbl_res.first.return_value = (mock_tbl, "public", "app_db", "en_US.UTF-8")
    tag_res = MagicMock()
    tag_res.all.return_value = []
    cat_res = MagicMock()
    cat_res.scalars.return_value.all.return_value = []
    col_tag_res = MagicMock()
    col_tag_res.all.return_value = []
    mock_session.execute = AsyncMock(side_effect=[tbl_res, tag_res, cat_res, col_tag_res])

    user_ctx = SimpleNamespace(is_superadmin=True)
    detail_dict = await search_service.get_table_details(
        session=mock_session,
        table_id=tbl_id,
        user_ctx=user_ctx,
    )
    assert detail_dict is not None
    detail = TableDetail(**detail_dict)
    assert len(detail.columns) == 1
    assert detail.columns[0].is_foreign_key is True
    assert detail.columns[0].referenced_table == "users"
    assert detail.columns[0].referenced_column == "id"


@pytest.mark.asyncio
async def test_get_catalog_objects_includes_fk_targets_in_indexes():
    from backend.src.api.v1.tables import get_catalog_objects
    from backend.src.models.catalog import TableType

    tbl_id = uuid.uuid4()
    idx_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    mock_tbl = SimpleNamespace(
        id=tbl_id,
        name="orders",
        table_type=TableType.TABLE,
        row_count_estimate=50,
        size_bytes=2048,
        source_description=None,
        approved_description=None,
        description_source="SOURCE_DB",
        owner="postgres",
        created_at=now,
    )
    mock_idx = SimpleNamespace(
        id=idx_id,
        table_id=tbl_id,
        name="idx_orders_user_id",
        index_type="BTREE",
        is_unique=False,
        is_primary=False,
        columns_list=["user_id"],
        definition="CREATE INDEX idx_orders_user_id ON public.orders (user_id)",
        created_at=now,
    )

    mock_db = AsyncMock()
    tbl_res = MagicMock()
    tbl_res.all.return_value = [(mock_tbl, "public", "app_db")]
    idx_res = MagicMock()
    idx_res.all.return_value = [(mock_idx, "orders", "public", "app_db")]
    fk_res = MagicMock()
    fk_res.all.return_value = [(tbl_id, "user_id", "users", "id")]
    routine_res = MagicMock()
    routine_res.all.return_value = []
    mock_db.execute = AsyncMock(side_effect=[tbl_res, idx_res, fk_res, routine_res])

    user_ctx = SimpleNamespace(is_superadmin=True)
    with patch("backend.src.api.v1.tables.build_table_rbac_filter", return_value=lambda q: q):
        resp = await get_catalog_objects(
            database=None,
            schema=None,
            table_id=None,
            object_type=None,
            user_ctx=user_ctx,
            db=mock_db,
        )

    assert len(resp.indexes) == 1
    fk_targets = resp.indexes[0].details.get("foreign_key_targets")
    assert fk_targets == [
        {
            "column": "user_id",
            "referenced_table": "users",
            "referenced_column": "id",
        }
    ]
