"""Metadata Connectors package providing pluggable database introspection."""

from backend.src.services.connectors.base import (
    BaseMetadataConnector,
    ColumnMetadata,
    ConnectionTestResult,
    ConnectorEngine,
    ForeignKeyMetadata,
    SchemaExtractionResult,
    SchemaMetadata,
    TableMetadata,
    TableStatisticsMetadata,
)
from backend.src.services.connectors.registry import (
    ConnectorRegistry,
    default_connector_registry,
)
import backend.src.services.connectors.postgres  # Auto-registers PostgresConnector

__all__ = [
    "BaseMetadataConnector",
    "ColumnMetadata",
    "ConnectionTestResult",
    "ConnectorEngine",
    "ConnectorRegistry",
    "ForeignKeyMetadata",
    "SchemaExtractionResult",
    "SchemaMetadata",
    "TableMetadata",
    "TableStatisticsMetadata",
    "default_connector_registry",
]
