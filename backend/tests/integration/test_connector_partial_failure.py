import asyncio
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.src.services.connectors.postgres import PostgresConnector
from backend.src.services.connectors.base import SchemaMetadata, SchemaExtractionResult


@pytest.mark.asyncio
async def test_postgres_connector_partial_failure_permission_denied(test_db_engine: AsyncEngine):
    """
    Verify schema-level PARTIAL_FAILURE isolation on 42501 InsufficientPrivilege:
    extract_all_schemas() isolates the failure per schema, stages successful schemas,
    and captures error codes and diagnostic messages for the failed schema.
    """
    schema_ok = "schema_partial_ok"
    schema_fail = "schema_partial_fail"

    async with test_db_engine.begin() as conn:
        await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema_ok};"))
        await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema_fail};"))
        await conn.execute(
            text(f"CREATE TABLE IF NOT EXISTS {schema_ok}.items (id serial PRIMARY KEY, name text);")
        )
        await conn.execute(
            text(f"CREATE TABLE IF NOT EXISTS {schema_fail}.secrets (id serial PRIMARY KEY, token text);")
        )

    url = test_db_engine.url.render_as_string(hide_password=False)
    connector = PostgresConnector({"url": url})

    async def mock_extract_schemas():
        return [
            SchemaMetadata(name=schema_ok, owner="postgres"),
            SchemaMetadata(name=schema_fail, owner="postgres"),
        ]

    real_extract = connector.extract_schema_metadata

    async def simulated_extract_metadata(schema_name: str) -> SchemaExtractionResult:
        if schema_name == schema_fail:
            class SimulatedPrivilegeError(Exception):
                pgcode = "42501"
                sqlstate = "42501"

            raise SimulatedPrivilegeError("permission denied for schema schema_partial_fail")
        return await real_extract(schema_name)

    connector.extract_schemas = mock_extract_schemas  # type: ignore
    connector.extract_schema_metadata = simulated_extract_metadata  # type: ignore

    async with connector:
        results = await connector.extract_all_schemas()

    assert len(results) == 2

    # Check successful schema
    ok_res = next((r for r in results if r.schema.name == schema_ok), None)
    assert ok_res is not None
    assert ok_res.success is True
    assert ok_res.error_message is None
    assert any(t.name == "items" for t in ok_res.tables)

    # Check failed schema
    fail_res = next((r for r in results if r.schema.name == schema_fail), None)
    assert fail_res is not None
    assert fail_res.success is False
    assert fail_res.error_code == "42501"
    assert "permission denied" in fail_res.error_message.lower()
    assert len(fail_res.tables) == 0

    # Cleanup schemas
    async with test_db_engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_ok} CASCADE;"))
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_fail} CASCADE;"))


@pytest.mark.asyncio
async def test_postgres_connector_partial_failure_query_timeout(test_db_engine: AsyncEngine):
    """
    Verify schema-level PARTIAL_FAILURE isolation on 57014 query_canceled / TimeoutError.
    """
    schema_ok = "schema_timeout_ok"
    schema_timeout = "schema_timeout_fail"

    async with test_db_engine.begin() as conn:
        await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema_ok};"))
        await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema_timeout};"))
        await conn.execute(
            text(f"CREATE TABLE IF NOT EXISTS {schema_ok}.orders (id serial PRIMARY KEY, total numeric);")
        )

    url = test_db_engine.url.render_as_string(hide_password=False)
    connector = PostgresConnector({"url": url})

    async def mock_extract_schemas():
        return [
            SchemaMetadata(name=schema_ok, owner="postgres"),
            SchemaMetadata(name=schema_timeout, owner="postgres"),
        ]

    real_extract = connector.extract_schema_metadata

    async def simulated_extract_metadata(schema_name: str) -> SchemaExtractionResult:
        if schema_name == schema_timeout:
            class SimulatedTimeoutError(Exception):
                pgcode = "57014"
                sqlstate = "57014"

            raise SimulatedTimeoutError("canceling statement due to statement timeout")
        return await real_extract(schema_name)

    connector.extract_schemas = mock_extract_schemas  # type: ignore
    connector.extract_schema_metadata = simulated_extract_metadata  # type: ignore

    async with connector:
        results = await connector.extract_all_schemas()

    assert len(results) == 2

    ok_res = next((r for r in results if r.schema.name == schema_ok), None)
    assert ok_res is not None
    assert ok_res.success is True
    assert any(t.name == "orders" for t in ok_res.tables)

    fail_res = next((r for r in results if r.schema.name == schema_timeout), None)
    assert fail_res is not None
    assert fail_res.success is False
    assert fail_res.error_code == "57014"
    assert "statement timeout" in fail_res.error_message.lower()

    async with test_db_engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_ok} CASCADE;"))
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_timeout} CASCADE;"))
