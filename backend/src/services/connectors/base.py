from __future__ import annotations

import enum
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Type

from sqlalchemy.pool import NullPool, Pool

logger = logging.getLogger(__name__)


class ConnectorEngine(str, enum.Enum):
    """Supported database connector engines."""

    POSTGRESQL = "POSTGRESQL"
    MSSQL = "MSSQL"
    ORACLE = "ORACLE"
    MYSQL = "MYSQL"
    CLICKHOUSE = "CLICKHOUSE"
    MONGODB = "MONGODB"


@dataclass(frozen=True)
class ConnectionTestResult:
    """Structured diagnostic result returned by connection test probe."""

    success: bool
    latency_ms: float
    dialect: str
    server_version: Optional[str]
    message: str
    diagnostic_details: Dict[str, Any] = field(default_factory=dict)
    tested_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class SchemaMetadata:
    """Introspected database schema metadata."""

    name: str
    description: Optional[str] = None
    owner: Optional[str] = None


@dataclass
class TableMetadata:
    """Introspected table or view metadata."""

    schema_name: str
    name: str
    table_type: str  # TABLE, VIEW, MATERIALIZED_VIEW, PARTITIONED_TABLE, EXTERNAL_TABLE
    row_count_estimate: int = 0
    size_bytes: int = 0
    source_description: Optional[str] = None
    owner: Optional[str] = None
    custom_properties: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ColumnMetadata:
    """Introspected column metadata."""

    schema_name: str
    table_name: str
    name: str
    ordinal_position: int
    data_type: str
    raw_data_type: str
    is_nullable: bool = True
    is_primary_key: bool = False
    is_foreign_key: bool = False
    default_value: Optional[str] = None
    source_description: Optional[str] = None


@dataclass
class ForeignKeyMetadata:
    """Introspected foreign key relationship metadata."""

    schema_name: str
    table_name: str
    column_name: str
    foreign_schema_name: str
    foreign_table_name: str
    foreign_column_name: str
    constraint_name: str


@dataclass
class TableStatisticsMetadata:
    """Observed routine table metrics (row count and size) without schema DDL change."""

    schema_name: str
    table_name: str
    row_count_estimate: int
    size_bytes: int


@dataclass
class IndexMetadata:
    """Introspected table index metadata."""

    schema_name: str
    table_name: str
    name: str
    is_unique: bool = False
    is_primary: bool = False
    index_type: str = "BTREE"
    columns_list: List[str] = field(default_factory=list)
    definition: Optional[str] = None


@dataclass
class RoutineMetadata:
    """Introspected stored procedure or function routine metadata."""

    schema_name: str
    name: str
    routine_type: str  # PROCEDURE, FUNCTION
    language: str = "sql"
    arguments_signature: Optional[str] = None
    return_type: Optional[str] = None
    source_description: Optional[str] = None


@dataclass
class SchemaExtractionResult:
    """Complete metadata bundle for a single database schema."""

    schema: SchemaMetadata
    tables: List[TableMetadata] = field(default_factory=list)
    columns: List[ColumnMetadata] = field(default_factory=list)
    foreign_keys: List[ForeignKeyMetadata] = field(default_factory=list)
    statistics: List[TableStatisticsMetadata] = field(default_factory=list)
    indexes: List[IndexMetadata] = field(default_factory=list)
    routines: List[RoutineMetadata] = field(default_factory=list)
    success: bool = True
    error_message: Optional[str] = None
    error_code: Optional[str] = None


class BaseMetadataConnector(ABC):
    """
    Abstract base class for all metadata connectors.

    Enforces:
    - Ephemeral connection lifecycle using NullPool (0 residual idle connections).
    - Session-level query timeout and read-only protections:
        SET statement_timeout = 30000;
        SET idle_in_transaction_session_timeout = 10000;
        SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY;
    - Standardized metadata extraction contract.
    """

    STATEMENT_TIMEOUT_MS: int = 30000
    IDLE_TIMEOUT_MS: int = 10000

    def __init__(self, connection_config: Dict[str, Any]) -> None:
        self.config: Dict[str, Any] = connection_config
        self._is_closed: bool = False

    @property
    def is_closed(self) -> bool:
        return self._is_closed

    def get_pool_class(self) -> Type[Pool]:
        """Returns NullPool to guarantee zero residual pooled connections."""
        return NullPool

    def get_session_setup_statements(self) -> List[str]:
        """SQL statements to execute upon acquiring a session to enforce security & timeouts."""
        return [
            f"SET statement_timeout = {self.STATEMENT_TIMEOUT_MS};",
            f"SET idle_in_transaction_session_timeout = {self.IDLE_TIMEOUT_MS};",
            "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY;",
        ]

    @abstractmethod
    async def __aenter__(self) -> "BaseMetadataConnector":
        """Establishes ephemeral connection using NullPool with strict timeouts."""
        self._is_closed = False
        return self

    @abstractmethod
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Immediately disconnects and releases TCP socket."""
        self._is_closed = True

    @abstractmethod
    async def test_connection(self) -> ConnectionTestResult:
        """Executes lightweight ping, validating auth, SSL, and read-only mode."""
        pass

    @abstractmethod
    async def extract_schemas(self) -> List[SchemaMetadata]:
        """Lists accessible schemas excluding system/internal schemas."""
        pass

    @abstractmethod
    async def extract_schema_metadata(self, schema_name: str) -> SchemaExtractionResult:
        """Bulk extracts tables, columns, constraints, and statistics for a single schema."""
        pass

    async def extract_all_schemas(self) -> List[SchemaExtractionResult]:
        """
        Iterates across all discoverable schemas with schema-level partial failure isolation.
        Captures exceptions per schema without failing remaining schemas.
        """
        schemas = await self.extract_schemas()
        results: List[SchemaExtractionResult] = []

        for schema_meta in schemas:
            try:
                extraction = await self.extract_schema_metadata(schema_meta.name)
                results.append(extraction)
            except Exception as exc:
                pgcode = getattr(exc, "pgcode", None) or getattr(exc, "sqlstate", None) or "EXTRACTION_ERROR"
                logger.error(
                    "Schema extraction failed for schema '%s' with code %s: %s",
                    schema_meta.name,
                    pgcode,
                    exc,
                    exc_info=True,
                )
                results.append(
                    SchemaExtractionResult(
                        schema=schema_meta,
                        tables=[],
                        columns=[],
                        foreign_keys=[],
                        statistics=[],
                        success=False,
                        error_message=str(exc),
                        error_code=str(pgcode),
                    )
                )

        return results
