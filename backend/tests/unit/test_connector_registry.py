import pytest
from typing import Any, Dict, List

from backend.src.models.connections import DatabaseEngine
from backend.src.services.connectors.base import (
    BaseMetadataConnector,
    ConnectionTestResult,
    SchemaExtractionResult,
    SchemaMetadata,
)
from backend.src.services.connectors.registry import ConnectorRegistry


class DummyPostgresConnector(BaseMetadataConnector):
    async def __aenter__(self) -> "DummyPostgresConnector":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        pass

    async def test_connection(self) -> ConnectionTestResult:
        return ConnectionTestResult(
            success=True,
            latency_ms=1.0,
            dialect="POSTGRESQL",
            server_version="16.0",
            message="OK",
        )

    async def extract_schemas(self) -> List[SchemaMetadata]:
        return []

    async def extract_schema_metadata(self, schema_name: str) -> SchemaExtractionResult:
        return SchemaExtractionResult(schema=SchemaMetadata(name=schema_name))


class DummyCustomConnector(BaseMetadataConnector):
    async def __aenter__(self) -> "DummyCustomConnector":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        pass

    async def test_connection(self) -> ConnectionTestResult:
        return ConnectionTestResult(
            success=True,
            latency_ms=2.0,
            dialect="CUSTOM",
            server_version="1.0",
            message="OK",
        )

    async def extract_schemas(self) -> List[SchemaMetadata]:
        return []

    async def extract_schema_metadata(self, schema_name: str) -> SchemaExtractionResult:
        return SchemaExtractionResult(schema=SchemaMetadata(name=schema_name))


def test_connector_registry_registration_and_retrieval():
    """Verify registry allows registering and retrieving connector classes."""
    registry = ConnectorRegistry()
    registry.register(DatabaseEngine.POSTGRESQL, DummyPostgresConnector)

    # Resolution by enum
    cls = registry.get_connector_class(DatabaseEngine.POSTGRESQL)
    assert cls is DummyPostgresConnector

    # Resolution by case-insensitive string
    cls_str = registry.get_connector_class("postgresql")
    assert cls_str is DummyPostgresConnector

    cls_str_upper = registry.get_connector_class("POSTGRESQL")
    assert cls_str_upper is DummyPostgresConnector


def test_connector_registry_factory_instantiation():
    """Verify create_connector instantiates connector with passed config."""
    registry = ConnectorRegistry()
    registry.register(DatabaseEngine.POSTGRESQL, DummyPostgresConnector)

    config = {"host": "127.0.0.1", "port": 5432, "database": "test_db"}
    connector = registry.create_connector(DatabaseEngine.POSTGRESQL, config)

    assert isinstance(connector, DummyPostgresConnector)
    assert connector.config == config


def test_connector_registry_unhandled_dialects():
    """Verify registry raises NotImplementedError or ValueError for unhandled/unregistered dialects."""
    registry = ConnectorRegistry()

    # Enum not registered yet
    with pytest.raises((NotImplementedError, ValueError)) as exc_info:
        registry.get_connector_class(DatabaseEngine.MSSQL)
    assert "MSSQL" in str(exc_info.value) or "unsupported" in str(exc_info.value).lower()

    # Unknown dialect string
    with pytest.raises((NotImplementedError, ValueError)) as exc_info:
        registry.create_connector("UNKNOWN_DIALECT", {})
    assert "UNKNOWN_DIALECT" in str(exc_info.value) or "unsupported" in str(exc_info.value).lower()


def test_connector_registry_custom_registration_hook():
    """Verify custom connectors can be registered dynamically."""
    registry = ConnectorRegistry()
    registry.register("CUSTOM_WAREHOUSE", DummyCustomConnector)

    connector = registry.create_connector("CUSTOM_WAREHOUSE", {"endpoint": "cloud"})
    assert isinstance(connector, DummyCustomConnector)
    assert connector.config["endpoint"] == "cloud"


def test_connector_registry_invalid_connector_class():
    """Verify registering a class not inheriting BaseMetadataConnector raises TypeError."""
    registry = ConnectorRegistry()
    class NotAConnector:
        pass

    with pytest.raises(TypeError):
        registry.register(DatabaseEngine.POSTGRESQL, NotAConnector)  # type: ignore
