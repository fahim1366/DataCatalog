import inspect
import pytest
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

from backend.src.services.connectors.base import (
    BaseMetadataConnector,
    ConnectionTestResult,
    ConnectorEngine,
    SchemaMetadata,
    TableMetadata,
    ColumnMetadata,
    ForeignKeyMetadata,
    TableStatisticsMetadata,
    SchemaExtractionResult,
)


def test_connector_domain_dataclasses_instantiation():
    """Verify all domain dataclasses instantiate with expected attributes and defaults."""
    # 1. ConnectionTestResult
    test_result = ConnectionTestResult(
        success=True,
        latency_ms=12.5,
        dialect="POSTGRESQL",
        server_version="PostgreSQL 16.2",
        message="Connection established successfully",
        diagnostic_details={"ssl": True},
    )
    assert test_result.success is True
    assert test_result.latency_ms == 12.5
    assert test_result.dialect == "POSTGRESQL"
    assert test_result.server_version == "PostgreSQL 16.2"
    assert test_result.diagnostic_details["ssl"] is True
    assert isinstance(test_result.tested_at, datetime)

    # 2. SchemaMetadata
    schema_meta = SchemaMetadata(
        name="public",
        description="Public schema",
        owner="postgres",
    )
    assert schema_meta.name == "public"
    assert schema_meta.description == "Public schema"
    assert schema_meta.owner == "postgres"

    # 3. TableMetadata
    table_meta = TableMetadata(
        schema_name="public",
        name="customers",
        table_type="TABLE",
        row_count_estimate=1500,
        size_bytes=65536,
        source_description="Customer entity table",
        owner="postgres",
        custom_properties={"tablespace": "pg_default"},
    )
    assert table_meta.name == "customers"
    assert table_meta.table_type == "TABLE"
    assert table_meta.row_count_estimate == 1500
    assert table_meta.size_bytes == 65536
    assert table_meta.custom_properties["tablespace"] == "pg_default"

    # 4. ColumnMetadata
    col_meta = ColumnMetadata(
        schema_name="public",
        table_name="customers",
        name="id",
        ordinal_position=1,
        data_type="uuid",
        raw_data_type="uuid",
        is_nullable=False,
        is_primary_key=True,
        is_foreign_key=False,
        default_value="gen_random_uuid()",
        source_description="Primary customer identifier",
    )
    assert col_meta.name == "id"
    assert col_meta.is_primary_key is True
    assert col_meta.is_nullable is False
    assert col_meta.ordinal_position == 1

    # 5. ForeignKeyMetadata
    fk_meta = ForeignKeyMetadata(
        schema_name="public",
        table_name="orders",
        column_name="customer_id",
        foreign_schema_name="public",
        foreign_table_name="customers",
        foreign_column_name="id",
        constraint_name="fk_orders_customers",
    )
    assert fk_meta.constraint_name == "fk_orders_customers"
    assert fk_meta.foreign_table_name == "customers"

    # 6. TableStatisticsMetadata
    stats_meta = TableStatisticsMetadata(
        schema_name="public",
        table_name="customers",
        row_count_estimate=1500,
        size_bytes=65536,
    )
    assert stats_meta.row_count_estimate == 1500
    assert stats_meta.size_bytes == 65536

    # 7. SchemaExtractionResult
    extraction_result = SchemaExtractionResult(
        schema=schema_meta,
        tables=[table_meta],
        columns=[col_meta],
        foreign_keys=[fk_meta],
        statistics=[stats_meta],
        success=True,
    )
    assert extraction_result.success is True
    assert len(extraction_result.tables) == 1
    assert len(extraction_result.columns) == 1
    assert len(extraction_result.foreign_keys) == 1
    assert len(extraction_result.statistics) == 1
    assert extraction_result.error_message is None


def test_base_metadata_connector_abstract_methods():
    """Verify BaseMetadataConnector cannot be instantiated directly and declares abstract interface."""
    with pytest.raises(TypeError) as exc_info:
        BaseMetadataConnector(connection_config={})
    
    msg = str(exc_info.value)
    # Must enforce abstract methods
    assert "__aenter__" in msg or "abstract" in msg
    assert "extract_schemas" in msg or "abstract" in msg


@pytest.mark.asyncio
async def test_base_metadata_connector_lifecycle_and_timeouts():
    """Verify concrete connector lifecycle, NullPool usage, and timeout session parameters."""
    from sqlalchemy.pool import NullPool

    class ConcreteConnector(BaseMetadataConnector):
        def __init__(self, connection_config: Dict[str, Any]):
            super().__init__(connection_config)
            self.closed = False

        async def __aenter__(self) -> "ConcreteConnector":
            await super().__aenter__()
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
            await super().__aexit__(exc_type, exc_val, exc_tb)
            self.closed = True

        async def test_connection(self) -> ConnectionTestResult:
            return ConnectionTestResult(
                success=True,
                latency_ms=5.0,
                dialect="TEST",
                server_version="1.0",
                message="OK",
            )

        async def extract_schemas(self) -> List[SchemaMetadata]:
            return [SchemaMetadata(name="public")]

        async def extract_schema_metadata(self, schema_name: str) -> SchemaExtractionResult:
            return SchemaExtractionResult(schema=SchemaMetadata(name=schema_name))

    config = {
        "host": "localhost",
        "port": 5432,
        "database": "testdb",
        "username": "postgres",
        "password": "secret",
    }
    connector = ConcreteConnector(config)
    assert connector.config == config

    # Check pool class is NullPool
    assert connector.get_pool_class() == NullPool

    # Check default session parameter statements
    session_params = connector.get_session_setup_statements()
    expected_statements = [
        "SET statement_timeout = 30000;",
        "SET idle_in_transaction_session_timeout = 10000;",
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY;",
    ]
    for stmt in expected_statements:
        assert stmt in session_params

    # Verify context manager lifecycle
    async with connector as ctx:
        assert ctx is connector
    assert connector.is_closed is True
