from __future__ import annotations

import asyncio
import logging
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Type

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool, Pool

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
from backend.src.services.connectors.registry import default_connector_registry

logger = logging.getLogger(__name__)


def _detect_mssql_driver() -> Optional[str]:
    """Detects available async MSSQL drivers."""
    try:
        import aioodbc  # noqa: F401
        return "aioodbc"
    except ImportError:
        pass
    try:
        import pymssql  # noqa: F401
        return "pymssql"
    except ImportError:
        pass
    return None


class MSSQLConnector(BaseMetadataConnector):
    """
    Enterprise Microsoft SQL Server (MSSQL) metadata connector.

    Uses ephemeral connections with NullPool, strict timeouts, and READ UNCOMMITTED isolation.
    Gracefully detects and falls back between aioodbc and pymssql.
    """

    STATEMENT_TIMEOUT_MS: int = 30000

    QUERIES: Dict[str, str] = {
        "TEST": """
            SELECT @@VERSION AS server_version, DB_NAME() AS current_database;
        """,
        "DISCOVER_DATABASES": """
            SELECT name 
            FROM sys.databases 
            WHERE state = 0 
              AND database_id > 4 
            ORDER BY name;
        """,
        "COLLATION": """
            SELECT CAST(DATABASEPROPERTYEX(DB_NAME(), 'Collation') AS VARCHAR(128)) AS collation_name;
        """,
        "SCHEMAS": """
            SELECT s.name AS schema_name, p.name AS owner
            FROM sys.schemas s
            JOIN sys.database_principals p ON s.principal_id = p.principal_id
            WHERE s.name NOT IN (
                'guest', 'INFORMATION_SCHEMA', 'sys', 'db_owner', 'db_accessadmin',
                'db_securityadmin', 'db_ddladmin', 'db_backupoperator', 'db_datareader',
                'db_datawriter', 'db_denydatareader', 'db_denydatawriter'
            )
            ORDER BY s.name;
        """,
        "TABLES": """
            SELECT 
                t.name AS table_name,
                CASE t.type WHEN 'U' THEN 'TABLE' WHEN 'V' THEN 'VIEW' ELSE 'TABLE' END AS table_type,
                ISNULL(p.row_count, 0) AS row_count_estimate,
                ISNULL(p.size_bytes, 0) AS size_bytes,
                CAST(ep.value AS NVARCHAR(MAX)) AS source_description,
                USER_NAME(t.schema_id) AS owner
            FROM (
                SELECT object_id, name, schema_id, type FROM sys.tables
                UNION ALL
                SELECT object_id, name, schema_id, type FROM sys.views
            ) t
            JOIN sys.schemas s ON t.schema_id = s.schema_id
            LEFT JOIN (
                SELECT 
                    object_id,
                    SUM(CASE WHEN index_id < 2 THEN row_count ELSE 0 END) AS row_count,
                    SUM(reserved_page_count) * 8192 AS size_bytes
                FROM sys.dm_db_partition_stats
                GROUP BY object_id
            ) p ON t.object_id = p.object_id
            LEFT JOIN sys.extended_properties ep ON ep.major_id = t.object_id AND ep.minor_id = 0 AND ep.name = 'MS_Description'
            WHERE s.name = :schema_name
            ORDER BY t.name;
        """,
        "COLUMNS": """
            SELECT 
                t.name AS table_name,
                c.name AS column_name,
                c.column_id AS ordinal_position,
                tp.name AS data_type,
                tp.name + CASE 
                    WHEN tp.name IN ('varchar', 'char', 'varbinary') THEN '(' + CASE WHEN c.max_length = -1 THEN 'max' ELSE CAST(c.max_length AS VARCHAR) END + ')'
                    WHEN tp.name IN ('nvarchar', 'nchar') THEN '(' + CASE WHEN c.max_length = -1 THEN 'max' ELSE CAST(c.max_length/2 AS VARCHAR) END + ')'
                    WHEN tp.name IN ('decimal', 'numeric') THEN '(' + CAST(c.precision AS VARCHAR) + ',' + CAST(c.scale AS VARCHAR) + ')'
                    ELSE ''
                END AS raw_data_type,
                c.collation_name AS collation,
                c.is_nullable AS is_nullable,
                CASE WHEN pk.column_id IS NOT NULL THEN 1 ELSE 0 END AS is_primary_key,
                CASE WHEN fk.column_id IS NOT NULL THEN 1 ELSE 0 END AS is_foreign_key,
                dc.definition AS default_value,
                CAST(ep.value AS NVARCHAR(MAX)) AS source_description
            FROM (
                SELECT object_id, name, schema_id FROM sys.tables
                UNION ALL
                SELECT object_id, name, schema_id FROM sys.views
            ) t
            JOIN sys.schemas s ON t.schema_id = s.schema_id
            JOIN sys.columns c ON t.object_id = c.object_id
            JOIN sys.types tp ON c.user_type_id = tp.user_type_id
            LEFT JOIN sys.default_constraints dc ON c.default_object_id = dc.object_id
            LEFT JOIN sys.extended_properties ep ON ep.major_id = t.object_id AND ep.minor_id = c.column_id AND ep.name = 'MS_Description'
            LEFT JOIN (
                SELECT ic.object_id, ic.column_id
                FROM sys.index_columns ic
                JOIN sys.indexes i ON ic.object_id = i.object_id AND ic.index_id = i.index_id
                WHERE i.is_primary_key = 1
            ) pk ON t.object_id = pk.object_id AND c.column_id = pk.column_id
            LEFT JOIN (
                SELECT fkc.parent_object_id AS object_id, fkc.parent_column_id AS column_id
                FROM sys.foreign_key_columns fkc
            ) fk ON t.object_id = fk.object_id AND c.column_id = fk.column_id
            WHERE s.name = :schema_name
            ORDER BY t.name, c.column_id;
        """,
        "FOREIGN_KEYS": """
            SELECT 
                tp.name AS table_name,
                cp.name AS column_name,
                rf_s.name AS foreign_schema_name,
                tr.name AS foreign_table_name,
                cr.name AS foreign_column_name,
                fk.name AS constraint_name
            FROM sys.foreign_keys fk
            JOIN sys.foreign_key_columns fkc ON fk.object_id = fkc.constraint_object_id
            JOIN sys.tables tp ON fkc.parent_object_id = tp.object_id
            JOIN sys.schemas s ON tp.schema_id = s.schema_id
            JOIN sys.columns cp ON fkc.parent_object_id = cp.object_id AND fkc.parent_column_id = cp.column_id
            JOIN sys.tables tr ON fkc.referenced_object_id = tr.object_id
            JOIN sys.schemas rf_s ON tr.schema_id = rf_s.schema_id
            JOIN sys.columns cr ON fkc.referenced_object_id = cr.object_id AND fkc.referenced_column_id = cr.column_id
            WHERE s.name = :schema_name
            ORDER BY tp.name, fk.name;
        """,
        "STATISTICS": """
            SELECT 
                t.name AS table_name,
                ISNULL(p.row_count, 0) AS row_count_estimate,
                ISNULL(p.size_bytes, 0) AS size_bytes
            FROM sys.tables t
            JOIN sys.schemas s ON t.schema_id = s.schema_id
            LEFT JOIN (
                SELECT 
                    object_id,
                    SUM(CASE WHEN index_id < 2 THEN row_count ELSE 0 END) AS row_count,
                    SUM(reserved_page_count) * 8192 AS size_bytes
                FROM sys.dm_db_partition_stats
                GROUP BY object_id
            ) p ON t.object_id = p.object_id
            WHERE s.name = :schema_name
            ORDER BY t.name;
        """,
        "INDEXES": """
            SELECT 
                t.name AS table_name,
                i.name AS index_name,
                CAST(i.is_unique AS BIT) AS is_unique,
                CAST(i.is_primary_key AS BIT) AS is_primary,
                i.type_desc AS index_type,
                (
                    SELECT STRING_AGG(c.name, ', ') WITHIN GROUP (ORDER BY ic.key_ordinal)
                    FROM sys.index_columns ic
                    JOIN sys.columns c ON ic.object_id = c.object_id AND ic.column_id = c.column_id
                    WHERE ic.object_id = i.object_id AND ic.index_id = i.index_id
                ) AS column_names
            FROM sys.indexes i
            JOIN sys.tables t ON i.object_id = t.object_id
            JOIN sys.schemas s ON t.schema_id = s.schema_id
            WHERE s.name = :schema_name AND i.name IS NOT NULL
            ORDER BY t.name, i.name;
        """,
        "ROUTINES": """
            SELECT 
                o.name AS routine_name,
                CASE o.type WHEN 'P' THEN 'PROCEDURE' ELSE 'FUNCTION' END AS routine_type,
                'SQL' AS language,
                CAST(ep.value AS NVARCHAR(MAX)) AS source_description
            FROM sys.objects o
            JOIN sys.schemas s ON o.schema_id = s.schema_id
            LEFT JOIN sys.extended_properties ep ON ep.major_id = o.object_id AND ep.minor_id = 0 AND ep.name = 'MS_Description'
            WHERE s.name = :schema_name AND o.type IN ('P', 'FN', 'IF', 'TF')
            ORDER BY o.name;
        """,
    }

    def __init__(self, connection_config: Dict[str, Any]) -> None:
        super().__init__(connection_config)
        self._engine: Optional[AsyncEngine] = None

    def get_pool_class(self) -> Type[Pool]:
        return NullPool

    def get_session_setup_statements(self) -> List[str]:
        return [
            "SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;",
            f"SET LOCK_TIMEOUT {self.STATEMENT_TIMEOUT_MS};",
        ]

    def build_connection_url(self) -> str:
        """Constructs an async driver connection URL."""
        if "url" in self.config:
            return self.config["url"]

        driver = _detect_mssql_driver()
        if not driver:
            raise MissingDriverError(
                engine="MSSQL",
                package_name="aioodbc",
                message="برای اتصال به پایگاه‌داده SQL Server، لطفاً پکیج 'aioodbc' یا 'pymssql' را نصب نمایید.",
            )

        host = self.config.get("host", "127.0.0.1")
        port = self.config.get("port", 1433)
        database = self.config.get("database") or self.config.get("database_name", "master")
        username = self.config.get("username") or self.config.get("user", "sa")
        password = self.config.get("password", "")

        quoted_username = urllib.parse.quote_plus(str(username))
        quoted_password = urllib.parse.quote_plus(str(password)) if password else ""
        auth = f"{quoted_username}:{quoted_password}@" if quoted_password else f"{quoted_username}@"

        custom_params = self.config.get("custom_params") or {}
        if driver == "aioodbc":
            odbc_driver = custom_params.get("driver", "ODBC Driver 18 for SQL Server")
            trust_cert = custom_params.get("TrustServerCertificate", "yes")
            params = f"?driver={urllib.parse.quote_plus(odbc_driver)}&TrustServerCertificate={trust_cert}"
            return f"mssql+aioodbc://{auth}{host}:{port}/{database}{params}"
        else:
            return f"mssql+pymssql://{auth}{host}:{port}/{database}"

    def _create_engine(self) -> AsyncEngine:
        url = self.build_connection_url()
        return create_async_engine(
            url,
            poolclass=self.get_pool_class(),
            connect_args={"timeout": 10},
            echo=False,
        )

    async def __aenter__(self) -> "MSSQLConnector":
        self._is_closed = False
        if self._engine is None:
            self._engine = self._create_engine()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
        self._is_closed = True

    async def test_connection(self) -> ConnectionTestResult:
        """5s timeout diagnostic ping returning SQL Server version (@@VERSION)."""
        start_time = time.monotonic()
        try:
            return await asyncio.wait_for(self._run_connection_test(start_time), timeout=5.0)
        except asyncio.TimeoutError:
            elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)
            return ConnectionTestResult(
                success=False,
                latency_ms=elapsed_ms,
                dialect="MSSQL",
                server_version=None,
                message="Connection probe timed out after 5.0 seconds.",
                diagnostic_details={"error_type": "TimeoutError", "timeout_seconds": 5.0},
            )
        except Exception as exc:
            elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)
            return ConnectionTestResult(
                success=False,
                latency_ms=elapsed_ms,
                dialect="MSSQL",
                server_version=None,
                message=str(exc),
                diagnostic_details={"error_type": type(exc).__name__, "error": str(exc)},
            )

    async def _run_connection_test(self, start_time: float) -> ConnectionTestResult:
        engine = self._engine or self._create_engine()
        should_dispose = self._engine is None
        try:
            async with engine.connect() as conn:
                for stmt in self.get_session_setup_statements():
                    await conn.execute(text(stmt))

                result = await conn.execute(text(self.QUERIES["TEST"]))
                row = result.fetchone()
                server_ver = row[0] if row else None
                db_name = row[1] if row else ""
                elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)

                return ConnectionTestResult(
                    success=True,
                    latency_ms=elapsed_ms,
                    dialect="MSSQL",
                    server_version=server_ver,
                    message=f"Successfully connected to SQL Server database '{db_name}'.",
                    diagnostic_details={
                        "version_string": server_ver or "",
                        "database": db_name,
                        "session_read_only": True,
                        "tcp_reachable": True,
                        "auth_valid": True,
                    },
                )
        finally:
            if should_dispose:
                await engine.dispose()

    async def discover_databases(self) -> List[str]:
        """Query sys.databases WHERE state = 0 AND database_id > 4."""
        engine = self._engine or self._create_engine()
        should_dispose = self._engine is None
        try:
            async with engine.connect() as conn:
                for stmt in self.get_session_setup_statements():
                    await conn.execute(text(stmt))
                result = await conn.execute(text(self.QUERIES["DISCOVER_DATABASES"]))
                return [r[0] for r in result.fetchall()]
        finally:
            if should_dispose:
                await engine.dispose()

    async def _run_database_discovery(self, start_time: float) -> Dict[str, Any]:
        engine = self._engine or self._create_engine()
        should_dispose = self._engine is None
        try:
            async with engine.connect() as conn:
                for stmt in self.get_session_setup_statements():
                    await conn.execute(text(stmt))

                ver_res = await conn.execute(text(self.QUERIES["TEST"]))
                ver_row = ver_res.fetchone()
                server_ver = ver_row[0] if ver_row else None

                db_res = await conn.execute(text(self.QUERIES["DISCOVER_DATABASES"]))
                databases = [r[0] for r in db_res.fetchall()]
                elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)
                return {
                    "success": True,
                    "databases": databases,
                    "latency_ms": elapsed_ms,
                    "server_version": server_ver,
                    "message": f"Successfully discovered {len(databases)} databases.",
                }
        finally:
            if should_dispose:
                await engine.dispose()

    async def extract_database_collation(self) -> Optional[str]:
        """Extracts SQL Server database default collation."""
        engine = self._engine or self._create_engine()
        should_dispose = self._engine is None
        try:
            async with engine.connect() as conn:
                for stmt in self.get_session_setup_statements():
                    await conn.execute(text(stmt))
                result = await conn.execute(text(self.QUERIES["COLLATION"]))
                row = result.fetchone()
                return str(row[0]) if row and row[0] else None
        except Exception as exc:
            logger.debug("MSSQL database collation extraction failed: %s", exc)
            return None
        finally:
            if should_dispose:
                await engine.dispose()

    async def extract_schemas(self) -> List[SchemaMetadata]:
        """Lists accessible schemas excluding system schemas."""
        engine = self._engine or self._create_engine()
        should_dispose = self._engine is None
        try:
            async with engine.connect() as conn:
                for stmt in self.get_session_setup_statements():
                    await conn.execute(text(stmt))
                result = await conn.execute(text(self.QUERIES["SCHEMAS"]))
                return [
                    SchemaMetadata(name=row["schema_name"], owner=row["owner"])
                    for row in result.mappings().all()
                ]
        finally:
            if should_dispose:
                await engine.dispose()

    async def extract_schema_metadata(self, schema_name: str) -> SchemaExtractionResult:
        """Bulk extracts tables, columns, constraints, statistics, indexes, and routines."""
        engine = self._engine or self._create_engine()
        should_dispose = self._engine is None
        try:
            async with engine.connect() as conn:
                for stmt in self.get_session_setup_statements():
                    await conn.execute(text(stmt))

                # Tables
                tbl_res = await conn.execute(text(self.QUERIES["TABLES"]), {"schema_name": schema_name})
                tables = [
                    TableMetadata(
                        schema_name=schema_name,
                        name=row["table_name"],
                        table_type=row["table_type"],
                        row_count_estimate=int(row["row_count_estimate"] or 0),
                        size_bytes=int(row["size_bytes"] or 0),
                        source_description=row["source_description"],
                        owner=row["owner"],
                    )
                    for row in tbl_res.mappings().all()
                ]

                # Columns
                col_res = await conn.execute(text(self.QUERIES["COLUMNS"]), {"schema_name": schema_name})
                columns = [
                    ColumnMetadata(
                        schema_name=schema_name,
                        table_name=row["table_name"],
                        name=row["column_name"],
                        ordinal_position=int(row["ordinal_position"]),
                        data_type=row["data_type"],
                        raw_data_type=row["raw_data_type"] or row["data_type"],
                        collation=row["collation"],
                        is_nullable=bool(row["is_nullable"]),
                        is_primary_key=bool(row["is_primary_key"]),
                        is_foreign_key=bool(row["is_foreign_key"]),
                        default_value=row["default_value"],
                        source_description=row["source_description"],
                    )
                    for row in col_res.mappings().all()
                ]

                # Foreign Keys
                fk_res = await conn.execute(text(self.QUERIES["FOREIGN_KEYS"]), {"schema_name": schema_name})
                foreign_keys = [
                    ForeignKeyMetadata(
                        schema_name=schema_name,
                        table_name=row["table_name"],
                        column_name=row["column_name"],
                        foreign_schema_name=row["foreign_schema_name"],
                        foreign_table_name=row["foreign_table_name"],
                        foreign_column_name=row["foreign_column_name"],
                        constraint_name=row["constraint_name"],
                    )
                    for row in fk_res.mappings().all()
                ]

                # Statistics
                stat_res = await conn.execute(text(self.QUERIES["STATISTICS"]), {"schema_name": schema_name})
                statistics = [
                    TableStatisticsMetadata(
                        schema_name=schema_name,
                        table_name=row["table_name"],
                        row_count_estimate=int(row["row_count_estimate"] or 0),
                        size_bytes=int(row["size_bytes"] or 0),
                    )
                    for row in stat_res.mappings().all()
                ]

                # Indexes
                idx_res = await conn.execute(text(self.QUERIES["INDEXES"]), {"schema_name": schema_name})
                indexes = [
                    IndexMetadata(
                        schema_name=schema_name,
                        table_name=row["table_name"],
                        name=row["index_name"],
                        is_unique=bool(row["is_unique"]),
                        is_primary=bool(row["is_primary"]),
                        index_type=row["index_type"] or "BTREE",
                        columns_list=[c.strip() for c in (row["column_names"] or "").split(",") if c.strip()],
                    )
                    for row in idx_res.mappings().all()
                ]

                # Routines
                rt_res = await conn.execute(text(self.QUERIES["ROUTINES"]), {"schema_name": schema_name})
                routines = [
                    RoutineMetadata(
                        schema_name=schema_name,
                        name=row["routine_name"],
                        routine_type=row["routine_type"],
                        language=row["language"] or "sql",
                        source_description=row["source_description"],
                    )
                    for row in rt_res.mappings().all()
                ]

                return SchemaExtractionResult(
                    schema=SchemaMetadata(name=schema_name),
                    tables=tables,
                    columns=columns,
                    foreign_keys=foreign_keys,
                    statistics=statistics,
                    indexes=indexes,
                    routines=routines,
                    success=True,
                )
        finally:
            if should_dispose:
                await engine.dispose()


# Auto-register with default registry
default_connector_registry.register(DatabaseEngine.MSSQL, MSSQLConnector)
default_connector_registry.register("MSSQL", MSSQLConnector)
