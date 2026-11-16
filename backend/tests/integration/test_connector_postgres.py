import asyncio
import time
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.src.services.connectors.postgres import PostgresConnector


@pytest.mark.asyncio
async def test_postgres_connector_bulk_extraction_and_nullpool_cleanup(test_db_engine: AsyncEngine):
    """
    Comprehensive PostgreSQL connector integration test against real PostgreSQL 16:
    1. Populates a test schema with 200 tables and 5,000 columns:
       - 199 regular tables + 1 partitioned table (PARTITIONED_TABLE)
       - 25 columns per table (total 5,000 columns)
       - Composite primary keys (tenant_id, id)
       - Foreign keys (t_2 -> t_1)
       - Table and column comments
    2. Runs PostgresConnector extraction:
       - Asserts all 200 tables extracted
       - Asserts all 5,000 columns extracted
       - Asserts partitioned table has table_type == 'PARTITIONED_TABLE'
       - Asserts composite PKs identified
       - Asserts foreign key metadata captured
       - Asserts native comments captured
       - Asserts performance SLA: extraction completed in < 2.5 seconds
    3. Verifies NullPool connection lifecycle:
       - Asserts 0 residual open connections from the connector after exit.
    """
    schema_name = "test_catalog_bulk_200"

    # Setup schema and populate 200 tables / 5,000 columns
    async with test_db_engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE;"))
        await conn.execute(text(f"CREATE SCHEMA {schema_name};"))

        # Build 199 tables with 25 columns each (tenant_id, id, code, status, amount, is_active, payload, created_at, updated_at + 16 text columns col_10..col_25)
        # Using PL/pgSQL for ultra-fast batched schema generation
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
        await conn.execute(text(plpgsql))

        # Add 1 Partitioned Table with 25 columns
        part_cols = "tenant_id int NOT NULL, id int NOT NULL, code varchar(50) NOT NULL, status varchar(20), amount numeric(12,2), is_active boolean DEFAULT true, payload jsonb DEFAULT '{}'::jsonb, created_at timestamptz DEFAULT clock_timestamp(), updated_at timestamptz"
        for j in range(10, 26):
            part_cols += f", col_{j} text"
        await conn.execute(
            text(f"CREATE TABLE {schema_name}.t_partitioned ({part_cols}) PARTITION BY RANGE (id);")
        )
        await conn.execute(
            text(f"COMMENT ON TABLE {schema_name}.t_partitioned IS 'Root partitioned table';")
        )

        # Add Foreign Key from t_2 to t_1
        await conn.execute(
            text(
                f"ALTER TABLE {schema_name}.t_2 ADD CONSTRAINT fk_t2_t1 "
                f"FOREIGN KEY (tenant_id, id) REFERENCES {schema_name}.t_1(tenant_id, id);"
            )
        )

        # Insert sample rows and ANALYZE to populate pg_stat_user_tables / reltuples
        await conn.execute(
            text(
                f"INSERT INTO {schema_name}.t_1 (tenant_id, id, code, status, amount) "
                f"VALUES (101, 1, 'ALPHA', 'ACTIVE', 99.50);"
            )
        )
        await conn.execute(text(f"ANALYZE {schema_name}.t_1;"))

    url = test_db_engine.url.render_as_string(hide_password=False)
    # Configure custom application_name to precisely trace connection lifecycle in pg_stat_activity
    connector = PostgresConnector({"url": url, "custom_params": {"application_name": "datacatalog_ephemeral_connector"}})

    # Execute extraction within context manager and measure elapsed time
    async with connector:
        # 1. Test connection probe
        test_probe = await connector.test_connection()
        assert test_probe.success is True
        assert test_probe.dialect == "POSTGRESQL"
        assert test_probe.latency_ms < 500.0

        # 2. Extract single schema metadata (bulk pg_catalog queries)
        start_time = time.monotonic()
        result = await connector.extract_schema_metadata(schema_name)
        extraction_time = time.monotonic() - start_time

    # Performance SLA: Under 2.5 seconds total
    assert extraction_time < 2.5, f"Bulk extraction took {extraction_time:.3f}s, exceeding 2.5s SLA."

    # Assert extraction success and counts
    assert result.success is True
    assert result.error_message is None
    assert len(result.tables) == 200, f"Expected 200 tables, got {len(result.tables)}"
    assert len(result.columns) == 5000, f"Expected 5,000 columns, got {len(result.columns)}"

    # Assert Partitioned table relkind mapped to PARTITIONED_TABLE
    part_table = next((t for t in result.tables if t.name == "t_partitioned"), None)
    assert part_table is not None
    assert part_table.table_type == "PARTITIONED_TABLE"
    assert part_table.source_description == "Root partitioned table"

    # Assert Table 1 metadata, comments, and row counts
    t1 = next((t for t in result.tables if t.name == "t_1"), None)
    assert t1 is not None
    assert t1.table_type == "TABLE"
    assert t1.source_description == "Table comment for t_1"
    assert t1.row_count_estimate >= 1

    # Assert Composite Primary Keys on Table 1
    t1_cols = [c for c in result.columns if c.table_name == "t_1"]
    assert len(t1_cols) == 25
    pk_cols = [c.name for c in t1_cols if c.is_primary_key]
    assert set(pk_cols) == {"tenant_id", "id"}, f"Expected composite PK (tenant_id, id), got {pk_cols}"

    # Assert Column comments and types
    code_col = next((c for c in t1_cols if c.name == "code"), None)
    assert code_col is not None
    assert code_col.source_description == "Code column comment for t_1"
    assert "varchar" in code_col.data_type.lower() or "character varying" in code_col.data_type.lower()
    assert code_col.is_nullable is False

    # Assert Foreign Key captured
    assert len(result.foreign_keys) >= 1
    fk = next((f for f in result.foreign_keys if f.constraint_name == "fk_t2_t1"), None)
    assert fk is not None
    assert fk.table_name == "t_2"
    assert fk.foreign_table_name == "t_1"

    # Assert Statistics captured
    assert len(result.statistics) == 200
    t1_stats = next((s for s in result.statistics if s.table_name == "t_1"), None)
    assert t1_stats is not None
    assert t1_stats.row_count_estimate >= 1
    assert t1_stats.size_bytes > 0

    # Verify NullPool lifecycle: connector is closed and engine disposed
    assert connector.is_closed is True
    assert connector._engine is None

    # Assert strictly 0 residual open connections from connector
    async with test_db_engine.connect() as conn:
        res = await conn.execute(
            text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() "
                "AND application_name = 'datacatalog_ephemeral_connector';"
            )
        )
        lingering_count = res.scalar()
        assert lingering_count == 0, f"Expected 0 residual open connections, found {lingering_count}"

    # Cleanup schema
    async with test_db_engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE;"))
