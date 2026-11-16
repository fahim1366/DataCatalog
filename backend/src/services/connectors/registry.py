from __future__ import annotations

import logging
from typing import Any, Dict, Type, Union

from backend.src.models.connections import DatabaseEngine
from backend.src.services.connectors.base import BaseMetadataConnector

logger = logging.getLogger(__name__)


class ConnectorRegistry:
    """
    Factory and registry for database metadata connectors.
    Maps database engine types to concrete BaseMetadataConnector implementations.
    """

    def __init__(self) -> None:
        self._registry: Dict[str, Type[BaseMetadataConnector]] = {}

    @staticmethod
    def _normalize_key(engine_type: Union[DatabaseEngine, str]) -> str:
        if isinstance(engine_type, DatabaseEngine):
            return engine_type.value.upper()
        return str(engine_type).strip().upper()

    def register(
        self,
        engine_type: Union[DatabaseEngine, str],
        connector_cls: Type[BaseMetadataConnector],
    ) -> None:
        """
        Registers a connector class for the given engine type.
        Raises TypeError if connector_cls is not a subclass of BaseMetadataConnector.
        """
        if not isinstance(connector_cls, type) or not issubclass(connector_cls, BaseMetadataConnector):
            raise TypeError(
                f"Connector class {connector_cls} must be a subclass of BaseMetadataConnector"
            )
        key = self._normalize_key(engine_type)
        self._registry[key] = connector_cls
        logger.info("Registered connector %s for engine '%s'", connector_cls.__name__, key)

    def get_connector_class(
        self, engine_type: Union[DatabaseEngine, str]
    ) -> Type[BaseMetadataConnector]:
        """
        Resolves the connector class for the given engine.
        Raises NotImplementedError if dialect is unsupported or not registered.
        """
        key = self._normalize_key(engine_type)
        if key not in self._registry:
            raise NotImplementedError(
                f"Unsupported or unregistered connector engine: '{engine_type}'. "
                f"Available engines: {list(self._registry.keys())}"
            )
        return self._registry[key]

    def create_connector(
        self,
        engine_type: Union[DatabaseEngine, str],
        connection_config: Dict[str, Any],
    ) -> BaseMetadataConnector:
        """
        Factory method instantiating a connector for the specified engine and configuration.
        """
        connector_cls = self.get_connector_class(engine_type)
        return connector_cls(connection_config=connection_config)


# Global default registry instance
default_connector_registry = ConnectorRegistry()
