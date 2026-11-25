from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from backend.src.models.connections import DatabaseEngine
from backend.src.services.connectors.base import (
    BaseMetadataConnector,
    ColumnMetadata,
    ConnectionTestResult,
    ForeignKeyMetadata,
    IndexMetadata,
    MissingDriverError,
    RoutineMetadata,
    SchemaExtractionResult,
    SchemaMetadata,
    TableMetadata,
    TableStatisticsMetadata,
)
from backend.src.services.connectors.registry import (
    ConnectorRegistry,
    default_connector_registry,
)
from backend.src.services.connectors.postgres import PostgresConnector
from backend.src.services.connectors.mssql import MSSQLConnector
from backend.src.services.connectors.mysql import MySQLConnector
from backend.src.services.connectors.oracle import OracleConnector
from backend.src.services.connectors.clickhouse import ClickHouseConnector
from backend.src.services.connectors.mongodb import MongoDBConnector
from backend.src.api.v1.connections import format_connection_error_persian


# ============================================================================
# 1. Registry Resolution & Factory Tests
# ============================================================================


def test_registry_contains_all_six_database_engines():
    """Verify default_connector_registry contains all 6 enterprise engines."""
    engines = [
        DatabaseEngine.POSTGRESQL,
        DatabaseEngine.MSSQL,
        DatabaseEngine.MYSQL,
        DatabaseEngine.ORACLE,
        DatabaseEngine.CLICKHOUSE,
        DatabaseEngine.MONGODB,
    ]
    expected_classes = {
        DatabaseEngine.POSTGRESQL: PostgresConnector,
        DatabaseEngine.MSSQL: MSSQLConnector,
        DatabaseEngine.MYSQL: MySQLConnector,
        DatabaseEngine.ORACLE: OracleConnector,
        DatabaseEngine.CLICKHOUSE: ClickHouseConnector,
        DatabaseEngine.MONGODB: MongoDBConnector,
    }

    for engine in engines:
        cls = default_connector_registry.get_connector_class(engine)
        assert cls is expected_classes[engine]
        # Also test string lookup
        assert default_connector_registry.get_connector_class(engine.value) is expected_classes[engine]
        assert default_connector_registry.get_connector_class(engine.value.lower()) is expected_classes[engine]


def test_registry_create_connector_instantiation():
    """Verify create_connector instantiates valid connector with passed config."""
    config = {"host": "10.0.0.1", "port": 1433, "username": "sa", "password": "pwd"}
    conn = default_connector_registry.create_connector(DatabaseEngine.MSSQL, config)
    assert isinstance(conn, MSSQLConnector)
    assert conn.config["host"] == "10.0.0.1"


# ============================================================================
# 2. Persian Error Formatting Tests for Missing Drivers and Contexts
# ============================================================================


def test_format_connection_error_persian_missing_drivers():
    """Verify friendly Persian driver error messages for all engines."""
    # MSSQL
    mssql_err = format_connection_error_persian("MissingDriverError: aioodbc driver not found", "test")
    assert "نصب" in mssql_err
    assert "aioodbc" in mssql_err or "SQL Server" in mssql_err

    # MySQL
    mysql_err = format_connection_error_persian("No module named 'aiomysql'", "test")
    assert "نصب" in mysql_err
    assert "aiomysql" in mysql_err

    # Oracle
    oracle_err = format_connection_error_persian("No module named 'oracledb'", "test")
    assert "نصب" in oracle_err
    assert "oracledb" in oracle_err

    # MongoDB
    mongo_err = format_connection_error_persian("No module named 'motor'", "test")
    assert "نصب" in mongo_err
    assert "motor" in mongo_err

    # ClickHouse
    ch_err = format_connection_error_persian("No module named 'clickhouse_connect'", "test")
    assert "نصب" in ch_err


# ============================================================================
# 3. MSSQL Connector Tests
# ============================================================================


@pytest.mark.asyncio
async def test_mssql_missing_driver_raises_error():
    """Verify MissingDriverError raised when neither aioodbc nor pymssql is installed."""
    with patch("backend.src.services.connectors.mssql._detect_mssql_driver", return_value=None):
        connector = MSSQLConnector({"host": "localhost", "port": 1433})
        with pytest.raises(MissingDriverError) as exc_info:
            connector.build_connection_url()
        assert "aioodbc" in str(exc_info.value) or "pymssql" in str(exc_info.value)


@pytest.mark.asyncio
async def test_mssql_test_connection_success():
    """Verify MSSQL test_connection queries @@VERSION and returns ConnectionTestResult."""
    mock_engine = MagicMock()
    mock_conn = AsyncMock()
    mock_engine.connect.return_value.__aenter__.return_value = mock_conn

    # Execute @@VERSION query
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = ("Microsoft SQL Server 2022 - 16.0.1000", "sales_db")
    mock_conn.execute.return_value = mock_cursor

    connector = MSSQLConnector({"host": "localhost", "port": 1433, "database": "sales_db"})
    connector._engine = mock_engine

    result = await connector.test_connection()
    assert result.success is True
    assert result.dialect == "MSSQL"
    assert "16.0.1000" in (result.server_version or "")
    assert "sales_db" in result.message


@pytest.mark.asyncio
async def test_mssql_discover_databases():
    """Verify MSSQL discover_databases queries sys.databases for non-system databases."""
    mock_engine = MagicMock()
    mock_conn = AsyncMock()
    mock_engine.connect.return_value.__aenter__.return_value = mock_conn

    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [("sales_db",), ("hr_db",), ("analytics",)]
    mock_conn.execute.return_value = mock_cursor

    connector = MSSQLConnector({"host": "localhost", "port": 1433})
    connector._engine = mock_engine

    dbs = await connector.discover_databases()
    assert dbs == ["sales_db", "hr_db", "analytics"]


@pytest.mark.asyncio
async def test_mssql_extract_schema_metadata():
    """Verify MSSQL full schema metadata extraction."""
    mock_engine = MagicMock()
    mock_conn = AsyncMock()
    mock_engine.connect.return_value.__aenter__.return_value = mock_conn

    def mock_execute(sql, params=None):
        cur = MagicMock()
        sql_str = str(sql)
        if "ISNULL(p.row_count, 0) AS row_count_estimate" in sql_str:
            cur.mappings.return_value.all.return_value = [
                {
                    "table_name": "orders",
                    "table_type": "TABLE",
                    "row_count_estimate": 5000,
                    "size_bytes": 102400,
                    "source_description": "Customer orders",
                    "owner": "dbo",
                }
            ]
        elif "tp.name AS data_type" in sql_str:
            cur.mappings.return_value.all.return_value = [
                {
                    "table_name": "orders",
                    "column_name": "id",
                    "ordinal_position": 1,
                    "data_type": "bigint",
                    "raw_data_type": "bigint",
                    "collation": None,
                    "is_nullable": False,
                    "is_primary_key": True,
                    "is_foreign_key": False,
                    "default_value": None,
                    "source_description": "PK",
                }
            ]
        elif "sys.foreign_keys" in sql_str:
            cur.mappings.return_value.all.return_value = []
        elif "sys.indexes" in sql_str:
            cur.mappings.return_value.all.return_value = [
                {
                    "table_name": "orders",
                    "index_name": "PK_orders",
                    "is_unique": True,
                    "is_primary": True,
                    "index_type": "CLUSTERED",
                    "column_names": "id",
                }
            ]
        elif "sys.objects" in sql_str:
            cur.mappings.return_value.all.return_value = []
        else:
            cur.mappings.return_value.all.return_value = []
        return cur

    mock_conn.execute.side_effect = mock_execute
    connector = MSSQLConnector({"host": "localhost", "port": 1433})
    connector._engine = mock_engine

    res = await connector.extract_schema_metadata("dbo")
    assert res.success is True
    assert len(res.tables) == 1
    assert res.tables[0].name == "orders"
    assert res.tables[0].row_count_estimate == 5000
    assert len(res.columns) == 1
    assert res.columns[0].is_primary_key is True
    assert len(res.indexes) == 1


# ============================================================================
# 4. MySQL Connector Tests
# ============================================================================


@pytest.mark.asyncio
async def test_mysql_missing_driver_raises_error():
    """Verify MissingDriverError when neither aiomysql nor asyncmy is installed."""
    with patch("backend.src.services.connectors.mysql._detect_mysql_driver", return_value=None):
        connector = MySQLConnector({"host": "localhost", "port": 3306})
        with pytest.raises(MissingDriverError) as exc_info:
            connector.build_connection_url()
        assert "aiomysql" in str(exc_info.value) or "asyncmy" in str(exc_info.value)


@pytest.mark.asyncio
async def test_mysql_test_connection_and_discovery():
    """Verify MySQL test_connection and discover_databases."""
    mock_engine = MagicMock()
    mock_conn = AsyncMock()
    mock_engine.connect.return_value.__aenter__.return_value = mock_conn

    test_cur = MagicMock()
    test_cur.fetchone.return_value = ("8.0.36", "ecommerce")
    mock_conn.execute.return_value = test_cur

    connector = MySQLConnector({"host": "localhost", "port": 3306, "database": "ecommerce"})
    connector._engine = mock_engine

    test_res = await connector.test_connection()
    assert test_res.success is True
    assert test_res.dialect == "MYSQL"
    assert "8.0.36" in (test_res.server_version or "")

    # Discovery
    disc_cur = MagicMock()
    disc_cur.fetchall.return_value = [("ecommerce",), ("inventory",)]
    mock_conn.execute.return_value = disc_cur

    dbs = await connector.discover_databases()
    assert dbs == ["ecommerce", "inventory"]


# ============================================================================
# 5. Oracle Connector Tests
# ============================================================================


@pytest.mark.asyncio
async def test_oracle_missing_driver_raises_error():
    """Verify MissingDriverError when oracledb is not installed."""
    with patch("backend.src.services.connectors.oracle._has_oracledb", return_value=False):
        connector = OracleConnector({"host": "localhost", "port": 1521})
        with pytest.raises(MissingDriverError) as exc_info:
            await connector._get_oracle_connection()
        assert "oracledb" in str(exc_info.value)


@pytest.mark.asyncio
async def test_oracle_test_connection_and_discovery():
    """Verify Oracle thin mode connection test and user schema discovery."""
    mock_conn = AsyncMock()
    mock_cursor = AsyncMock()
    mock_conn.cursor.return_value = mock_cursor

    mock_cursor.fetchone.side_effect = [
        ("Oracle Database 19c Enterprise Edition Release 19.0.0.0.0",),  # version
        ("HR",),  # current_schema
    ]
    mock_cursor.fetchall.return_value = [("HR",), ("SCOTT",), ("FINANCE",)]

    connector = OracleConnector({"host": "localhost", "port": 1521, "custom_params": {"service_name": "ORCLPDB1"}})
    connector._connection = mock_conn

    test_res = await connector.test_connection()
    assert test_res.success is True
    assert test_res.dialect == "ORACLE"
    assert "19c" in (test_res.server_version or "")

    schemas = await connector.discover_databases()
    assert schemas == ["HR", "SCOTT", "FINANCE"]


# ============================================================================
# 6. ClickHouse Connector Tests (HTTP Interface)
# ============================================================================


@pytest.mark.asyncio
async def test_clickhouse_test_connection_and_discovery():
    """Verify ClickHouse connection test and system.databases discovery."""
    connector = ClickHouseConnector({"host": "localhost", "port": 8123})

    async def mock_query_json(sql: str):
        if "version()" in sql:
            return [{"ver": "24.3.1.2672", "current_db": "default"}]
        if "system.databases" in sql:
            return [{"name": "analytics"}, {"name": "events"}]
        if "system.tables" in sql:
            return [
                {
                    "name": "hits",
                    "engine": "MergeTree",
                    "total_rows": 1000000,
                    "total_bytes": 50000000,
                    "comment": "Web hit logs",
                }
            ]
        if "system.columns" in sql:
            return [
                {
                    "table": "hits",
                    "name": "event_time",
                    "position": 1,
                    "type": "DateTime",
                    "default_expression": "",
                    "is_in_primary_key": 1,
                    "comment": "Timestamp",
                }
            ]
        return []

    with patch.object(connector, "_query_json", side_effect=mock_query_json):
        test_res = await connector.test_connection()
        assert test_res.success is True
        assert test_res.dialect == "CLICKHOUSE"
        assert "24.3.1" in (test_res.server_version or "")

        dbs = await connector.discover_databases()
        assert dbs == ["analytics", "events"]

        meta = await connector.extract_schema_metadata("analytics")
        assert meta.success is True
        assert len(meta.tables) == 1
        assert meta.tables[0].name == "hits"
        assert meta.tables[0].row_count_estimate == 1000000
        assert len(meta.columns) == 1
        assert meta.columns[0].is_primary_key is True
        assert len(meta.indexes) == 1


# ============================================================================
# 7. MongoDB Connector Tests
# ============================================================================


@pytest.mark.asyncio
async def test_mongodb_missing_driver_raises_error():
    """Verify MissingDriverError when motor/pymongo is not installed."""
    with patch("backend.src.services.connectors.mongodb._has_mongo_driver", return_value=False):
        connector = MongoDBConnector({"host": "localhost", "port": 27017})
        with pytest.raises(MissingDriverError) as exc_info:
            await connector._get_client()
        assert "motor" in str(exc_info.value) or "pymongo" in str(exc_info.value)


@pytest.mark.asyncio
async def test_mongodb_test_connection_and_schema_sampling():
    """Verify MongoDB ping, database discovery, and 50-document sampling type inference."""
    mock_client = MagicMock()
    mock_admin = AsyncMock()
    mock_admin.command.return_value = {"ok": 1.0}
    mock_client.admin = mock_admin
    mock_client.server_info = AsyncMock(return_value={"version": "7.0.5"})
    mock_client.list_database_names = AsyncMock(return_value=["admin", "config", "local", "store_db", "logs_db"])

    # Mock collection and document sampling
    mock_db = MagicMock()
    mock_client.__getitem__.return_value = mock_db
    mock_db.list_collection_names = AsyncMock(return_value=["users", "orders"])
    mock_db.command = AsyncMock(return_value={"count": 250, "size": 32000})

    class AsyncDocIterator:
        def __init__(self, docs):
            self.docs = docs
            self.idx = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.idx < len(self.docs):
                item = self.docs[self.idx]
                self.idx += 1
                return item
            raise StopAsyncIteration

    mock_coll = MagicMock()
    mock_db.__getitem__.return_value = mock_coll
    sample_docs = [
        {"_id": "obj1", "username": "alice", "age": 30, "is_active": True, "tags": ["a", "b"]},
        {"_id": "obj2", "username": "bob", "age": 25, "is_active": False, "address": {"city": "Tehran"}},
    ]
    mock_cursor = MagicMock()
    mock_cursor.__aiter__.side_effect = lambda: AsyncDocIterator(sample_docs)
    mock_coll.find.return_value.limit.return_value = mock_cursor
    mock_coll.index_information = AsyncMock(return_value={"_id_": {"key": [("_id", 1)]}})

    connector = MongoDBConnector({"host": "localhost", "port": 27017})
    connector._client = mock_client

    test_res = await connector.test_connection()
    assert test_res.success is True
    assert test_res.dialect == "MONGODB"
    assert test_res.server_version == "7.0.5"

    dbs = await connector.discover_databases()
    assert dbs == ["store_db", "logs_db"]
    assert "admin" not in dbs

    meta = await connector.extract_schema_metadata("store_db")
    assert meta.success is True
    assert len(meta.tables) >= 1
    # Check field type inference on users collection
    user_cols = {c.name: c for c in meta.columns}
    assert "_id" in user_cols
    assert user_cols["_id"].is_primary_key is True
    assert "username" in user_cols
    assert "string" in user_cols["username"].data_type


# ============================================================================
# 8. API Endpoint Universal Discovery & Collation Tests
# ============================================================================


@pytest.mark.asyncio
async def test_discover_databases_dynamic_delegation_all_engines():
    """Verify discover-databases endpoint delegates dynamically to any registered engine."""
    from backend.src.api.v1.connections import discover_databases
    from backend.src.schemas.connections import DiscoverDatabasesRequest

    engines = [
        DatabaseEngine.POSTGRESQL,
        DatabaseEngine.MSSQL,
        DatabaseEngine.MYSQL,
        DatabaseEngine.ORACLE,
        DatabaseEngine.CLICKHOUSE,
        DatabaseEngine.MONGODB,
    ]

    for engine in engines:
        connector_cls = default_connector_registry.get_connector_class(engine)
        mock_result = {
            "success": True,
            "databases": ["db1", "db2"],
            "latency_ms": 15.0,
            "server_version": "1.0",
            "message": "OK",
        }

        with patch.object(connector_cls, "discover_databases_from_config", new=AsyncMock(return_value=mock_result)):
            req = DiscoverDatabasesRequest(
                engine_type=engine,
                host="192.168.1.100",
                port=5432,
                credentials={"username": "user", "password": "pwd"},
            )
            resp = await discover_databases(payload=req, user_ctx=MagicMock())
            assert resp.success is True
            assert resp.databases == ["db1", "db2"]
            assert "2" in resp.message and "یافت شد" in resp.message


@pytest.mark.asyncio
async def test_discover_databases_handles_missing_driver_gracefully():
    """Verify discover-databases returns Persian error message on missing driver error."""
    from backend.src.api.v1.connections import discover_databases
    from backend.src.schemas.connections import DiscoverDatabasesRequest

    connector_cls = default_connector_registry.get_connector_class(DatabaseEngine.MSSQL)
    with patch.object(
        connector_cls,
        "discover_databases_from_config",
        side_effect=MissingDriverError("MSSQL", "aioodbc", "درایور aioodbc نصب نیست."),
    ):
        req = DiscoverDatabasesRequest(
            engine_type=DatabaseEngine.MSSQL,
            host="192.168.1.100",
            port=1433,
            credentials={"username": "sa", "password": "pwd"},
        )
        resp = await discover_databases(payload=req, user_ctx=MagicMock())
        assert resp.success is False
        assert "درایور" in resp.message or "نصب" in resp.message


@pytest.mark.asyncio
async def test_collation_extractions():
    """Verify collation extractions across relational engines."""
    # 1. MSSQL Collation
    mock_engine = MagicMock()
    mock_conn = AsyncMock()
    mock_engine.connect.return_value.__aenter__.return_value = mock_conn
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = ("SQL_Latin1_General_CP1_CI_AS",)
    mock_conn.execute.return_value = mock_cur

    mssql = MSSQLConnector({"host": "localhost", "port": 1433})
    mssql._engine = mock_engine
    col_mssql = await mssql.extract_database_collation()
    assert col_mssql == "SQL_Latin1_General_CP1_CI_AS"

    # 2. MySQL Collation
    mock_cur.fetchone.return_value = ("utf8mb4_0900_ai_ci",)
    mysql = MySQLConnector({"host": "localhost", "port": 3306})
    mysql._engine = mock_engine
    col_mysql = await mysql.extract_database_collation()
    assert col_mysql == "utf8mb4_0900_ai_ci"

