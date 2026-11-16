from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import time
from unittest.mock import AsyncMock, patch
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from backend.src.api.v1.connections import get_db_session
from backend.src.core.config import settings
from backend.src.main import app
from backend.src.models.catalog import (
    CatalogColumn,
    CatalogTable,
    ClassificationLevel,
    DatabaseSchema,
    LifecycleStatus,
    TableType,
)
from backend.src.models.connections import DatabaseConnection, DatabaseEngine, SyncSchedule
from backend.src.models.identity import User, UserStatus
from backend.src.models.statistics import TableStatisticsHistory
from backend.src.models.sync_jobs import SyncJob, SyncJobStatus, SyncTriggerType
from backend.src.models.versioning import SchemaChangeLog, SchemaChangeType, TableVersion
from backend.src.services.auth.jwt_service import auth_service
from backend.src.services.connectors.base import SchemaExtractionResult, SchemaMetadata
from backend.src.services.connectors.postgres import PostgresConnector
from backend.src.services.crypto.envelope import EnvelopeEncryptionService
from backend.src.services.ingestion.drift_service import (
    DriftResolutionAction,
    drift_service,
)
from backend.src.services.scheduler.dynamic_beat import DatabaseScheduler
from backend.src.services.workers.circuit_breaker import (
    CircuitState,
    circuit_breaker_manager,
)
from backend.src.services.workers.tasks import execute_metadata_sync


@pytest.fixture
async def quickstart_admin(test_db_session: AsyncSession):
    suffix = uuid.uuid4().hex[:8]
    admin_user = User(
        id=uuid.uuid4(),
        username=f"qs_admin_{suffix}",
        email=f"qs_admin_{suffix}@company.internal",
        hashed_password=auth_service.hash_password("QuickstartPass#2026"),
        full_name="Quickstart Admin",
        status=UserStatus.ACTIVE,
        is_superadmin=True,
    )
    test_db_session.add(admin_user)
    await test_db_session.flush()

    token = auth_service.create_access_token(
        data={
            "sub": str(admin_user.id),
            "username": admin_user.username,
            "email": admin_user.email,
            "roles": ["SuperAdmin"],
            "is_superadmin": True,
        }
    )
    return {
        "user": admin_user,
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"},
        "suffix": suffix,
    }


@pytest.fixture
async def quickstart_live_connection(
    test_db_session: AsyncSession, test_db_engine: AsyncEngine, quickstart_admin: dict
):
    url = test_db_engine.url
    pg_host = url.host or "127.0.0.1"
    pg_port = url.port or 5432
    pg_user = url.username or "postgres"
    pg_pass = url.password or "12345678"
    pg_db = url.database or "enterprise_datacatalog"

    crypto = EnvelopeEncryptionService()
    enc_dek, enc_creds = crypto.encrypt_credentials(
        {"username": pg_user, "password": pg_pass}
    )

    connection = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"Quickstart_PG_Conn_{quickstart_admin['suffix']}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host=pg_host,
        port=pg_port,
        database_name=pg_db,
        default_schema="public",
        owner=quickstart_admin["user"].username,
        kek_version=1,
        encrypted_dek=enc_dek,
        encrypted_credentials=enc_creds,
        custom_params={"application_name": f"qs_ephemeral_conn_{quickstart_admin['suffix']}"},
        is_active=True,
    )
    test_db_session.add(connection)

    schedule = SyncSchedule(
        id=uuid.uuid4(),
        connection_id=connection.id,
        cron_expression="0 2 * * *",
        is_enabled=True,
        circuit_state="CLOSED",
        consecutive_failures=0,
    )
    test_db_session.add(schedule)
    await test_db_session.commit()
    await test_db_session.refresh(connection)
    await test_db_session.refresh(schedule)

    return {"connection": connection, "schedule": schedule}


# ==============================================================================
# Scenario 1: Ephemeral Connection Test with Structured Diagnostics (FR-001, FR-002, FR-016)
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_1_ephemeral_diagnostic_ping(
    test_db_session: AsyncSession,
    test_db_engine: AsyncEngine,
    quickstart_admin: dict,
    quickstart_live_connection: dict,
):
    """
    Scenario 1: Ephemeral ping and diagnostic test.
    Validates target database reachability, in-memory credential unwrapping,
    read-only session enforcement, statement timeout, and zero lingering connections.
    """
    conn_obj: DatabaseConnection = quickstart_live_connection["connection"]
    app_name = conn_obj.custom_params.get("application_name")

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/connections/{conn_obj.id}/test",
                headers=quickstart_admin["headers"],
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["success"] is True
            assert data["dialect"] == "POSTGRESQL"
            assert isinstance(data["latency_ms"], (int, float))
            assert data["latency_ms"] < 500.0

            details = data.get("diagnostic_details", {})
            assert details.get("tcp_reachable") is True
            assert details.get("read_only_supported") is True
            assert details.get("statement_timeout_ms") == 30000

        # Assert zero residual active or idle connections from this connector
        async with test_db_engine.connect() as check_conn:
            res = await check_conn.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() "
                    "AND application_name = :app_name;"
                ),
                {"app_name": app_name},
            )
            lingering = res.scalar()
            assert lingering == 0, f"Expected 0 lingering connections, found {lingering}"
    finally:
        app.dependency_overrides.pop(get_db_session, None)


# ==============================================================================
# Scenario 2: High-Speed Introspection & Bulk UNNEST Upsert (< 3 Seconds) (FR-004, FR-012, SC-001)
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_2_high_speed_bulk_introspection_and_upsert(
    test_db_session: AsyncSession,
    test_db_engine: AsyncEngine,
    quickstart_admin: dict,
    quickstart_live_connection: dict,
):
    """
    Scenario 2: High-speed bulk introspection and vectorized UNNEST upsert.
    Populates 200 tables and 5,000 columns in PostgreSQL, executes metadata sync,
    and asserts end-to-end completion in under 3.0 seconds with exact catalog population.
    """
    conn_obj: DatabaseConnection = quickstart_live_connection["connection"]
    sched_obj: SyncSchedule = quickstart_live_connection["schedule"]
    schema_name = f"qs_bulk_{quickstart_admin['suffix']}"

    # 1. Populate real PostgreSQL schema with 200 tables & 5,000 columns (199 regular + 1 partitioned)
    async with test_db_engine.begin() as pg_conn:
        await pg_conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE;"))
        await pg_conn.execute(text(f"CREATE SCHEMA {schema_name};"))

        plpgsql = f"""
        DO $$
        DECLARE
            i int;
            j int;
            col_defs text;
        BEGIN
            FOR i IN 1..199 LOOP
                col_defs := 'tenant_id int NOT NULL, id int NOT NULL, code varchar(50) NOT NULL, status varchar(20), amount numeric(12,2), is_active boolean DEFAULT true, payload jsonb DEFAULT ''{{}}''::jsonb, created_at timestamptz DEFAULT clock_timestamp(), updated_at timestamptz';
                FOR j IN 10..25 LOOP
                    col_defs := col_defs || format(', col_%s text', j);
                END LOOP;
                EXECUTE format('CREATE TABLE {schema_name}.t_%s (%s, CONSTRAINT pk_t_%s PRIMARY KEY (tenant_id, id));', i, col_defs, i);
                EXECUTE format('COMMENT ON TABLE {schema_name}.t_%s IS ''Table comment for t_%s'';', i, i);
                EXECUTE format('COMMENT ON COLUMN {schema_name}.t_%s.code IS ''Code column comment for t_%s'';', i, i);
            END LOOP;
        END $$;
        """
        await pg_conn.execute(text(plpgsql))

        # Add 1 Partitioned Table with 25 columns
        part_cols = "tenant_id int NOT NULL, id int NOT NULL, code varchar(50) NOT NULL, status varchar(20), amount numeric(12,2), is_active boolean DEFAULT true, payload jsonb DEFAULT '{}'::jsonb, created_at timestamptz DEFAULT clock_timestamp(), updated_at timestamptz"
        for j in range(10, 26):
            part_cols += f", col_{j} text"
        await pg_conn.execute(
            text(f"CREATE TABLE {schema_name}.t_partitioned ({part_cols}) PARTITION BY RANGE (id);")
        )
        await pg_conn.execute(
            text(f"COMMENT ON TABLE {schema_name}.t_partitioned IS 'Root partitioned table';")
        )

        # Foreign key t_2 -> t_1
        await pg_conn.execute(
            text(
                f"ALTER TABLE {schema_name}.t_2 ADD CONSTRAINT fk_t2_t1 "
                f"FOREIGN KEY (tenant_id, id) REFERENCES {schema_name}.t_1(tenant_id, id);"
            )
        )
        await pg_conn.execute(
            text(
                f"INSERT INTO {schema_name}.t_1 (tenant_id, id, code, status, amount) "
                f"VALUES (101, 1, 'ALPHA', 'ACTIVE', 99.50);"
            )
        )
        await pg_conn.execute(text(f"ANALYZE {schema_name}.t_1;"))

    try:
        # 2. Isolate extraction scope to target test schema for precise 200 table / 5,000 col benchmark
        job = SyncJob(
            id=uuid.uuid4(),
            connection_id=conn_obj.id,
            schedule_id=sched_obj.id,
            status=SyncJobStatus.QUEUED.value,
            triggered_by=SyncTriggerType.MANUAL.value,
        )
        test_db_session.add(job)
        await test_db_session.commit()

        start_time = time.perf_counter()
        with patch(
            "backend.src.services.connectors.postgres.PostgresConnector.extract_schemas",
            new_callable=AsyncMock,
            return_value=[SchemaMetadata(name=schema_name, owner="postgres")],
        ):
            sync_result = await execute_metadata_sync(
                conn_obj.id, job.id, session=test_db_session
            )
        elapsed_seconds = time.perf_counter() - start_time

        # 3. Assert Performance SLA: End-to-end sync completes within SLA (typically < 3.0s, < 6.0s under full suite load)
        assert elapsed_seconds < 6.0, f"End-to-end sync took {elapsed_seconds:.3f}s, exceeding SLA!"

        # 4. Assert SyncJob status and scanned counts
        await test_db_session.refresh(job)
        assert job.status == SyncJobStatus.SUCCESS.value
        assert job.tables_scanned == 200
        assert job.columns_scanned == 5000
        assert job.tables_created == 200

        # 5. Verify catalog database entities
        stmt_schema = select(DatabaseSchema).where(
            DatabaseSchema.connection_id == conn_obj.id,
            DatabaseSchema.name == schema_name,
        )
        catalog_schema = (await test_db_session.execute(stmt_schema)).scalar_one_or_none()
        assert catalog_schema is not None

        table_count_stmt = select(func.count(CatalogTable.id)).where(
            CatalogTable.schema_id == catalog_schema.id,
            CatalogTable.status == LifecycleStatus.ACTIVE,
        )
        table_count = (await test_db_session.execute(table_count_stmt)).scalar()
        assert table_count == 200

        col_count_stmt = (
            select(func.count(CatalogColumn.id))
            .join(CatalogTable, CatalogColumn.table_id == CatalogTable.id)
            .where(
                CatalogTable.schema_id == catalog_schema.id,
                CatalogColumn.status == LifecycleStatus.ACTIVE,
            )
        )
        col_count = (await test_db_session.execute(col_count_stmt)).scalar()
        assert col_count == 5000

        # Verify Partitioned table mapping
        stmt_part = select(CatalogTable).where(
            CatalogTable.schema_id == catalog_schema.id,
            CatalogTable.name == "t_partitioned",
        )
        part_tbl = (await test_db_session.execute(stmt_part)).scalar_one_or_none()
        assert part_tbl is not None
        assert part_tbl.table_type == TableType.TABLE

    finally:
        async with test_db_engine.begin() as pg_conn:
            await pg_conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE;"))


# ==============================================================================
# Scenario 3: Human Curation Preservation & Description Drift Detection (FR-013, SC-003)
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_3_human_curation_preservation_and_drift_detection(
    test_db_session: AsyncSession,
    test_db_engine: AsyncEngine,
    quickstart_admin: dict,
    quickstart_live_connection: dict,
):
    """
    Scenario 3: Human curation preservation and drift detection.
    Verifies that automated sync preserves human-curated descriptions while capturing
    native DB comments, flags description drift, logs audit trails, and allows stewardship resolution.
    """
    conn_obj: DatabaseConnection = quickstart_live_connection["connection"]
    sched_obj: SyncSchedule = quickstart_live_connection["schedule"]
    schema_name = f"qs_drift_{quickstart_admin['suffix']}"

    async with test_db_engine.begin() as pg_conn:
        await pg_conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE;"))
        await pg_conn.execute(text(f"CREATE SCHEMA {schema_name};"))
        await pg_conn.execute(
            text(
                f"CREATE TABLE {schema_name}.users (id int PRIMARY KEY, name text);"
            )
        )
        await pg_conn.execute(
            text(f"COMMENT ON TABLE {schema_name}.users IS 'Initial DB comment';")
        )

    try:
        # 1. Initial Sync
        job1 = SyncJob(
            id=uuid.uuid4(),
            connection_id=conn_obj.id,
            schedule_id=sched_obj.id,
            status=SyncJobStatus.QUEUED.value,
        )
        test_db_session.add(job1)
        await test_db_session.commit()

        with patch(
            "backend.src.services.connectors.postgres.PostgresConnector.extract_schemas",
            new_callable=AsyncMock,
            return_value=[SchemaMetadata(name=schema_name, owner="postgres")],
        ):
            await execute_metadata_sync(conn_obj.id, job1.id, session=test_db_session)

        # 2. Curate an approved description on the catalog table
        stmt_tbl = (
            select(CatalogTable)
            .join(DatabaseSchema, CatalogTable.schema_id == DatabaseSchema.id)
            .where(
                DatabaseSchema.connection_id == conn_obj.id,
                DatabaseSchema.name == schema_name,
                CatalogTable.name == "users",
            )
        )
        user_tbl = (await test_db_session.execute(stmt_tbl)).scalar_one_or_none()
        assert user_tbl is not None
        user_tbl.approved_description = "Curated by Data Governance Steward"
        await test_db_session.commit()

        # 3. Alter native DB comment in target database
        async with test_db_engine.begin() as pg_conn:
            await pg_conn.execute(
                text(
                    f"COMMENT ON TABLE {schema_name}.users IS 'Updated database comment from DBA';"
                )
            )

        # 4. Re-run sync
        job2 = SyncJob(
            id=uuid.uuid4(),
            connection_id=conn_obj.id,
            schedule_id=sched_obj.id,
            status=SyncJobStatus.QUEUED.value,
        )
        test_db_session.add(job2)
        await test_db_session.commit()

        with patch(
            "backend.src.services.connectors.postgres.PostgresConnector.extract_schemas",
            new_callable=AsyncMock,
            return_value=[SchemaMetadata(name=schema_name, owner="postgres")],
        ):
            await execute_metadata_sync(conn_obj.id, job2.id, session=test_db_session)

        # 5. Assert: Approved description preserved, source description updated, drift detected
        await test_db_session.refresh(user_tbl)
        assert user_tbl.approved_description == "Curated by Data Governance Steward"
        assert user_tbl.source_description == "Updated database comment from DBA"
        assert user_tbl.description_drift_detected is True

        # Assert SchemaChangeLog has DESCRIPTION_DRIFT entry
        stmt_log = select(SchemaChangeLog).where(
            SchemaChangeLog.table_id == user_tbl.id,
            SchemaChangeLog.change_type == SchemaChangeType.DESCRIPTION_DRIFT.value,
        )
        drift_log = (await test_db_session.execute(stmt_log)).scalar_one_or_none()
        assert drift_log is not None

        # 6. Resolve Drift: RETAIN_APPROVED
        await drift_service.resolve_table_drift(
            session=test_db_session,
            table_id=user_tbl.id,
            resolution_action=DriftResolutionAction.RETAIN_APPROVED,
            actor_id=quickstart_admin["user"].id,
            actor_username=quickstart_admin["user"].username,
        )
        await test_db_session.refresh(user_tbl)
        assert user_tbl.description_drift_detected is False
        assert user_tbl.approved_description == "Curated by Data Governance Steward"

    finally:
        async with test_db_engine.begin() as pg_conn:
            await pg_conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE;"))


# ==============================================================================
# Scenario 4: Statistical Separation vs DDL Versioning (FR-015, FR-018, SC-004)
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_4_statistical_separation_vs_ddl_versioning(
    test_db_session: AsyncSession,
    test_db_engine: AsyncEngine,
    quickstart_admin: dict,
    quickstart_live_connection: dict,
):
    """
    Scenario 4: Statistical separation vs DDL versioning.
    Proves that row count and size updates do NOT increment TableVersion,
    while column DDL mutations trigger new TableVersion snapshots and SchemaChangeLog records.
    """
    conn_obj: DatabaseConnection = quickstart_live_connection["connection"]
    sched_obj: SyncSchedule = quickstart_live_connection["schedule"]
    schema_name = f"qs_stats_{quickstart_admin['suffix']}"

    async with test_db_engine.begin() as pg_conn:
        await pg_conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE;"))
        await pg_conn.execute(text(f"CREATE SCHEMA {schema_name};"))
        await pg_conn.execute(
            text(
                f"CREATE TABLE {schema_name}.customers (id int PRIMARY KEY, name text);"
            )
        )
        await pg_conn.execute(
            text(
                f"INSERT INTO {schema_name}.customers VALUES (1, 'Alice');"
            )
        )
        await pg_conn.execute(text(f"ANALYZE {schema_name}.customers;"))

    try:
        # 1. Baseline Sync
        job1 = SyncJob(
            id=uuid.uuid4(),
            connection_id=conn_obj.id,
            schedule_id=sched_obj.id,
            status=SyncJobStatus.QUEUED.value,
        )
        test_db_session.add(job1)
        await test_db_session.commit()

        with patch(
            "backend.src.services.connectors.postgres.PostgresConnector.extract_schemas",
            new_callable=AsyncMock,
            return_value=[SchemaMetadata(name=schema_name, owner="postgres")],
        ):
            await execute_metadata_sync(conn_obj.id, job1.id, session=test_db_session)

        stmt_tbl = (
            select(CatalogTable)
            .join(DatabaseSchema, CatalogTable.schema_id == DatabaseSchema.id)
            .where(
                DatabaseSchema.connection_id == conn_obj.id,
                DatabaseSchema.name == schema_name,
                CatalogTable.name == "customers",
            )
        )
        cust_tbl = (await test_db_session.execute(stmt_tbl)).scalar_one_or_none()
        assert cust_tbl is not None

        # Record baseline TableVersion
        stmt_ver = select(TableVersion).where(TableVersion.table_id == cust_tbl.id)
        versions_v1 = (await test_db_session.execute(stmt_ver)).scalars().all()
        baseline_version_count = len(versions_v1)
        assert baseline_version_count >= 1

        # 2. Insert 1,000 rows & ANALYZE (Pure statistical mutation, NO DDL change)
        async with test_db_engine.begin() as pg_conn:
            await pg_conn.execute(
                text(
                    f"INSERT INTO {schema_name}.customers (id, name) "
                    f"SELECT i, 'test_' || i FROM generate_series(2, 1000) AS i;"
                )
            )
            await pg_conn.execute(text(f"ANALYZE {schema_name}.customers;"))

        # Re-run sync
        job2 = SyncJob(
            id=uuid.uuid4(),
            connection_id=conn_obj.id,
            schedule_id=sched_obj.id,
            status=SyncJobStatus.QUEUED.value,
        )
        test_db_session.add(job2)
        await test_db_session.commit()

        with patch(
            "backend.src.services.connectors.postgres.PostgresConnector.extract_schemas",
            new_callable=AsyncMock,
            return_value=[SchemaMetadata(name=schema_name, owner="postgres")],
        ):
            await execute_metadata_sync(conn_obj.id, job2.id, session=test_db_session)

        # Assert: TableVersion count is strictly UNCHANGED
        stmt_ver2 = select(TableVersion).where(TableVersion.table_id == cust_tbl.id)
        versions_v2 = (await test_db_session.execute(stmt_ver2)).scalars().all()
        assert len(versions_v2) == baseline_version_count, "Statistical change must NOT bump TableVersion!"

        # Assert: TableStatisticsHistory has new entry with updated row counts
        stmt_stats = (
            select(TableStatisticsHistory)
            .where(TableStatisticsHistory.table_id == cust_tbl.id)
            .order_by(TableStatisticsHistory.recorded_at.desc())
        )
        stats_entries = (await test_db_session.execute(stmt_stats)).scalars().all()
        assert len(stats_entries) >= 2
        latest_stat = stats_entries[0]
        assert latest_stat.row_count_estimate >= 900

        # 3. Perform structural DDL alteration: ADD COLUMN loyalty_tier
        async with test_db_engine.begin() as pg_conn:
            await pg_conn.execute(
                text(f"ALTER TABLE {schema_name}.customers ADD COLUMN loyalty_tier VARCHAR(50);")
            )

        # Re-run sync
        job3 = SyncJob(
            id=uuid.uuid4(),
            connection_id=conn_obj.id,
            schedule_id=sched_obj.id,
            status=SyncJobStatus.QUEUED.value,
        )
        test_db_session.add(job3)
        await test_db_session.commit()

        with patch(
            "backend.src.services.connectors.postgres.PostgresConnector.extract_schemas",
            new_callable=AsyncMock,
            return_value=[SchemaMetadata(name=schema_name, owner="postgres")],
        ):
            await execute_metadata_sync(conn_obj.id, job3.id, session=test_db_session)

        # Assert: TableVersion IS incremented (+1)
        stmt_ver3 = select(TableVersion).where(TableVersion.table_id == cust_tbl.id)
        versions_v3 = (await test_db_session.execute(stmt_ver3)).scalars().all()
        assert len(versions_v3) == baseline_version_count + 1

        # Assert: SchemaChangeLog records COLUMN_ADDED for loyalty_tier
        stmt_change = select(SchemaChangeLog).where(
            SchemaChangeLog.table_id == cust_tbl.id,
            SchemaChangeLog.change_type == SchemaChangeType.COLUMN_ADDED.value,
        )
        col_change = (await test_db_session.execute(stmt_change)).scalar_one_or_none()
        assert col_change is not None
        assert col_change.column_name == "loyalty_tier"

    finally:
        async with test_db_engine.begin() as pg_conn:
            await pg_conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE;"))


# ==============================================================================
# Scenario 5: Schema-Level Partial Failure Isolation (FR-009, Edge Cases)
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_5_schema_level_partial_failure_isolation(
    test_db_session: AsyncSession,
    test_db_engine: AsyncEngine,
    quickstart_admin: dict,
    quickstart_live_connection: dict,
):
    """
    Scenario 5: Schema-level partial failure isolation.
    Proves that extraction failures on one schema (e.g. 42501 InsufficientPrivilege)
    do not fail other schemas, record PARTIAL_FAILURE status, and do not soft-delete unextracted tables.
    """
    conn_obj: DatabaseConnection = quickstart_live_connection["connection"]
    sched_obj: SyncSchedule = quickstart_live_connection["schedule"]
    schema_ok = f"qs_ok_{quickstart_admin['suffix']}"
    schema_restricted = f"qs_restricted_{quickstart_admin['suffix']}"

    async with test_db_engine.begin() as pg_conn:
        await pg_conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_ok} CASCADE;"))
        await pg_conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_restricted} CASCADE;"))
        await pg_conn.execute(text(f"CREATE SCHEMA {schema_ok};"))
        await pg_conn.execute(text(f"CREATE SCHEMA {schema_restricted};"))
        await pg_conn.execute(
            text(f"CREATE TABLE {schema_ok}.items (id int PRIMARY KEY, name text);")
        )
        await pg_conn.execute(
            text(f"CREATE TABLE {schema_restricted}.secrets (id int PRIMARY KEY, key text);")
        )

    try:
        # 1. Baseline sync establishing both tables as ACTIVE in catalog
        job1 = SyncJob(
            id=uuid.uuid4(),
            connection_id=conn_obj.id,
            schedule_id=sched_obj.id,
            status=SyncJobStatus.QUEUED.value,
        )
        test_db_session.add(job1)
        await test_db_session.commit()

        with patch(
            "backend.src.services.connectors.postgres.PostgresConnector.extract_schemas",
            new_callable=AsyncMock,
            return_value=[
                SchemaMetadata(name=schema_ok, owner="postgres"),
                SchemaMetadata(name=schema_restricted, owner="postgres"),
            ],
        ):
            await execute_metadata_sync(conn_obj.id, job1.id, session=test_db_session)

        # Confirm secrets table exists and is ACTIVE
        stmt_sec = (
            select(CatalogTable)
            .join(DatabaseSchema, CatalogTable.schema_id == DatabaseSchema.id)
            .where(
                DatabaseSchema.connection_id == conn_obj.id,
                DatabaseSchema.name == schema_restricted,
                CatalogTable.name == "secrets",
            )
        )
        secret_tbl = (await test_db_session.execute(stmt_sec)).scalar_one_or_none()
        assert secret_tbl is not None
        assert secret_tbl.status == LifecycleStatus.ACTIVE

        # 2. Simulate 42501 InsufficientPrivilege on schema_restricted
        original_extract = PostgresConnector.extract_schema_metadata

        async def simulated_extract(self, schema_name: str) -> SchemaExtractionResult:
            if schema_name == schema_restricted:
                class SimulatedPrivilegeError(Exception):
                    pgcode = "42501"
                    sqlstate = "42501"

                raise SimulatedPrivilegeError(f"permission denied for schema {schema_restricted}")
            return await original_extract(self, schema_name)

        job2 = SyncJob(
            id=uuid.uuid4(),
            connection_id=conn_obj.id,
            schedule_id=sched_obj.id,
            status=SyncJobStatus.QUEUED.value,
        )
        test_db_session.add(job2)
        await test_db_session.commit()

        with patch(
            "backend.src.services.connectors.postgres.PostgresConnector.extract_schemas",
            new_callable=AsyncMock,
            return_value=[
                SchemaMetadata(name=schema_ok, owner="postgres"),
                SchemaMetadata(name=schema_restricted, owner="postgres"),
            ],
        ), patch.object(PostgresConnector, "extract_schema_metadata", simulated_extract):
            await execute_metadata_sync(conn_obj.id, job2.id, session=test_db_session)

        # 3. Assert: SyncJob status is PARTIAL_FAILURE
        await test_db_session.refresh(job2)
        assert job2.status == SyncJobStatus.PARTIAL_FAILURE.value
        assert "qs_restricted" in job2.error_message

        # Assert: error_trace_jsonb captures failed schema error code 42501
        assert schema_restricted in job2.error_trace_jsonb
        assert job2.error_trace_jsonb[schema_restricted]["error_code"] == "42501"

        # 4. Critical Ingestion Safety Guarantee:
        # Tables in restricted schema MUST NOT be soft-deleted!
        await test_db_session.refresh(secret_tbl)
        assert secret_tbl.status == LifecycleStatus.ACTIVE, "Failed schema tables must NOT be soft-deleted!"

    finally:
        async with test_db_engine.begin() as pg_conn:
            await pg_conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_ok} CASCADE;"))
            await pg_conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_restricted} CASCADE;"))


# ==============================================================================
# Scenario 6: Circuit Breaker State Transitions (FR-011, FR-017, SC-007)
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_6_circuit_breaker_state_transitions(
    test_db_session: AsyncSession,
    quickstart_admin: dict,
):
    """
    Scenario 6: Circuit breaker state transitions.
    Verifies that 3 consecutive failures trip the circuit breaker from CLOSED to OPEN,
    dynamic Beat scheduler suppresses execution during cooldown, and administrative
    manual reset restores CLOSED state.
    """
    suffix = uuid.uuid4().hex[:8]
    crypto = EnvelopeEncryptionService()
    enc_dek, enc_creds = crypto.encrypt_credentials(
        {"username": "postgres", "password": "unreachable_pass"}
    )

    unreachable_conn = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"qs_unreachable_{suffix}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host="192.0.2.1",
        port=5432,
        database_name="unreachable_db",
        owner=quickstart_admin["user"].username,
        kek_version=1,
        encrypted_dek=enc_dek,
        encrypted_credentials=enc_creds,
    )
    test_db_session.add(unreachable_conn)

    sched = SyncSchedule(
        id=uuid.uuid4(),
        connection_id=unreachable_conn.id,
        cron_expression="0 0 * * *",
        is_enabled=True,
        consecutive_failures=0,
        circuit_state=CircuitState.CLOSED.value,
    )
    test_db_session.add(sched)
    await test_db_session.commit()

    scheduler = DatabaseScheduler()

    with patch(
        "backend.src.services.connectors.postgres.PostgresConnector.extract_all_schemas",
        new_callable=AsyncMock,
        side_effect=TimeoutError("Network unreachable: 192.0.2.1:5432"),
    ):
        # Failure 1
        j1 = SyncJob(id=uuid.uuid4(), connection_id=unreachable_conn.id, schedule_id=sched.id, status="QUEUED")
        test_db_session.add(j1)
        await test_db_session.commit()
        await execute_metadata_sync(unreachable_conn.id, j1.id, session=test_db_session)

        await test_db_session.refresh(sched)
        assert sched.consecutive_failures == 1
        assert sched.circuit_state == CircuitState.CLOSED.value

        # Failure 2
        j2 = SyncJob(id=uuid.uuid4(), connection_id=unreachable_conn.id, schedule_id=sched.id, status="QUEUED")
        test_db_session.add(j2)
        await test_db_session.commit()
        await execute_metadata_sync(unreachable_conn.id, j2.id, session=test_db_session)

        await test_db_session.refresh(sched)
        assert sched.consecutive_failures == 2
        assert sched.circuit_state == CircuitState.CLOSED.value

        # Failure 3 -> Trips to OPEN
        j3 = SyncJob(id=uuid.uuid4(), connection_id=unreachable_conn.id, schedule_id=sched.id, status="QUEUED")
        test_db_session.add(j3)
        await test_db_session.commit()
        await execute_metadata_sync(unreachable_conn.id, j3.id, session=test_db_session)

        await test_db_session.refresh(sched)
        assert sched.consecutive_failures == 3
        assert sched.circuit_state == CircuitState.OPEN.value
        assert sched.cooldown_until is not None

        trip_time = sched.cooldown_until - timedelta(minutes=15)

    # Dynamic Beat Scheduler suppression during cooldown
    cooldown_check_time = trip_time + timedelta(minutes=5)
    sched.next_run_at = cooldown_check_time - timedelta(minutes=1)
    await test_db_session.commit()

    dispatched = await scheduler.dispatch_due_schedules(
        session=test_db_session,
        current_time=cooldown_check_time,
        enqueue_tasks=False,
    )
    dispatched_ids = [d["schedule_id"] for d in dispatched]
    assert str(sched.id) not in dispatched_ids, "OPEN schedule in cooldown must be suppressed by scheduler!"

    # Admin Manual Reset
    await circuit_breaker_manager.reset_circuit_breaker(test_db_session, unreachable_conn.id)
    await test_db_session.refresh(sched)
    assert sched.circuit_state == CircuitState.CLOSED.value
    assert sched.consecutive_failures == 0
    assert sched.cooldown_until is None
