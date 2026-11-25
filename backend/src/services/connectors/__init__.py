"""Metadata Connectors package providing pluggable database introspection."""

from backend.src.services.connectors.base import (
    BaseMetadataConnector,
    ColumnMetadata,
    ConnectionTestResult,
    ConnectorEngine,
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

# Auto-register all concrete dialect connectors
import backend.src.services.connectors.postgres  # Auto-registers PostgresConnector
import backend.src.services.connectors.mssql     # Auto-registers MSSQLConnector
import backend.src.services.connectors.mysql     # Auto-registers MySQLConnector
import backend.src.services.connectors.oracle    # Auto-registers OracleConnector
import backend.src.services.connectors.clickhouse  # Auto-registers ClickHouseConnector
import backend.src.services.connectors.mongodb   # Auto-registers MongoDBConnector

from backend.src.services.connectors.postgres import PostgresConnector
from backend.src.services.connectors.mssql import MSSQLConnector
from backend.src.services.connectors.mysql import MySQLConnector
from backend.src.services.connectors.oracle import OracleConnector
from backend.src.services.connectors.clickhouse import ClickHouseConnector
from backend.src.services.connectors.mongodb import MongoDBConnector

__all__ = [
    "BaseMetadataConnector",
    "ColumnMetadata",
    "ConnectionTestResult",
    "ConnectorEngine",
    "ConnectorRegistry",
    "ForeignKeyMetadata",
    "IndexMetadata",
    "MissingDriverError",
    "RoutineMetadata",
    "SchemaExtractionResult",
    "SchemaMetadata",
    "TableMetadata",
    "TableStatisticsMetadata",
    "default_connector_registry",
    "PostgresConnector",
    "MSSQLConnector",
    "MySQLConnector",
    "OracleConnector",
    "ClickHouseConnector",
    "MongoDBConnector",
]
