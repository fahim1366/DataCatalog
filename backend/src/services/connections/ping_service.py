from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import asyncpg

from backend.src.models.connections import DatabaseEngine

logger = logging.getLogger(__name__)


@dataclass
class PingResult:
    """Structured diagnostic and latency result of a live connection test."""

    success: bool
    latency_ms: float
    dialect: DatabaseEngine
    message: str
    diagnostic_details: Optional[Dict[str, Any]] = field(default_factory=dict)
    tested_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class PingService:
    """
    Lightweight asynchronous database dialect ping service.

    Supported dialects:
    - POSTGRESQL: Direct asyncpg protocol handshake executing 'SELECT 1'.
    - MSSQL / MYSQL / ORACLE / CLICKHOUSE: High-performance async socket/driver probe.

    All probes are strictly bounded by asyncio.wait_for with a 5.0-second timeout ceiling.
    Zero unhandled exceptions: all network, credential, or protocol failures return structured diagnostics.
    """

    DEFAULT_TIMEOUT: float = 5.0

    async def ping(
        self,
        engine_type: DatabaseEngine,
        host: str,
        port: int,
        database_name: str,
        credentials: Optional[Dict[str, Any]] = None,
        custom_params: Optional[Dict[str, Any]] = None,
        timeout_seconds: float = DEFAULT_TIMEOUT,
    ) -> PingResult:
        """
        Executes a dialect-specific connectivity test within the specified timeout.
        """
        start_time = time.monotonic()
        credentials = credentials or {}
        custom_params = custom_params or {}

        try:
            # Enforce strict 5.0-second timeout ceiling
            return await asyncio.wait_for(
                self._dispatch_dialect_ping(
                    engine_type=engine_type,
                    host=host,
                    port=port,
                    database_name=database_name,
                    credentials=credentials,
                    custom_params=custom_params,
                    start_time=start_time,
                ),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)
            logger.warning(
                "Connection test timed out after %.2fms for %s at %s:%s",
                elapsed_ms,
                engine_type.value,
                host,
                port,
            )
            return PingResult(
                success=False,
                latency_ms=elapsed_ms,
                dialect=engine_type,
                message=f"Connection timed out after {timeout_seconds:.1f}s to {host}:{port}",
                diagnostic_details={
                    "error_type": "TimeoutError",
                    "host": host,
                    "port": port,
                    "database_name": database_name,
                    "error": f"Dialect ping exceeded strict {timeout_seconds:.1f}s timeout ceiling.",
                },
                tested_at=datetime.now(timezone.utc),
            )
        except Exception as exc:
            elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)
            logger.error(
                "Unexpected failure testing %s at %s:%s: %s",
                engine_type.value,
                host,
                port,
                exc,
                exc_info=True,
            )
            return PingResult(
                success=False,
                latency_ms=elapsed_ms,
                dialect=engine_type,
                message=f"Connection test error: {str(exc)}",
                diagnostic_details={
                    "error_type": type(exc).__name__,
                    "host": host,
                    "port": port,
                    "database_name": database_name,
                    "error": str(exc),
                },
                tested_at=datetime.now(timezone.utc),
            )

    async def _dispatch_dialect_ping(
        self,
        engine_type: DatabaseEngine,
        host: str,
        port: int,
        database_name: str,
        credentials: Dict[str, Any],
        custom_params: Dict[str, Any],
        start_time: float,
    ) -> PingResult:
        if engine_type == DatabaseEngine.POSTGRESQL:
            return await self._ping_postgresql(
                host=host,
                port=port,
                database_name=database_name,
                credentials=credentials,
                custom_params=custom_params,
                start_time=start_time,
            )
        else:
            return await self._ping_socket_probe(
                engine_type=engine_type,
                host=host,
                port=port,
                database_name=database_name,
                start_time=start_time,
            )

    async def _ping_postgresql(
        self,
        host: str,
        port: int,
        database_name: str,
        credentials: Dict[str, Any],
        custom_params: Dict[str, Any],
        start_time: float,
    ) -> PingResult:
        username = credentials.get("username")
        password = credentials.get("password")
        ssl = custom_params.get("ssl", False)
        if custom_params.get("sslmode") == "require":
            ssl = True

        try:
            conn = await asyncpg.connect(
                host=host,
                port=port,
                user=username,
                password=password,
                database=database_name,
                ssl=ssl,
                timeout=4.5,
            )
            try:
                res = await conn.fetchval("SELECT 1")
                elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)
                return PingResult(
                    success=True,
                    latency_ms=elapsed_ms,
                    dialect=DatabaseEngine.POSTGRESQL,
                    message="Connection successful (SELECT 1 executed)",
                    diagnostic_details={
                        "query": "SELECT 1",
                        "server_version": str(conn.get_server_version()),
                    },
                    tested_at=datetime.now(timezone.utc),
                )
            finally:
                await conn.close()
        except Exception as exc:
            elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)
            return PingResult(
                success=False,
                latency_ms=elapsed_ms,
                dialect=DatabaseEngine.POSTGRESQL,
                message=f"PostgreSQL connection failed: {str(exc)}",
                diagnostic_details={
                    "error_type": type(exc).__name__,
                    "host": host,
                    "port": port,
                    "database_name": database_name,
                    "error": str(exc),
                },
                tested_at=datetime.now(timezone.utc),
            )

    async def _ping_socket_probe(
        self,
        engine_type: DatabaseEngine,
        host: str,
        port: int,
        database_name: str,
        start_time: float,
    ) -> PingResult:
        try:
            reader, writer = await asyncio.open_connection(host=host, port=port)
            writer.close()
            await writer.wait_closed()
            elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)
            return PingResult(
                success=True,
                latency_ms=elapsed_ms,
                dialect=engine_type,
                message=f"Socket probe to {engine_type.value} service succeeded",
                diagnostic_details={
                    "probe_type": "TCP_SOCKET_PROBE",
                    "host": host,
                    "port": port,
                    "database_name": database_name,
                },
                tested_at=datetime.now(timezone.utc),
            )
        except Exception as exc:
            elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)
            return PingResult(
                success=False,
                latency_ms=elapsed_ms,
                dialect=engine_type,
                message=f"{engine_type.value} probe failed: {str(exc)}",
                diagnostic_details={
                    "error_type": type(exc).__name__,
                    "host": host,
                    "port": port,
                    "database_name": database_name,
                    "error": str(exc),
                },
                tested_at=datetime.now(timezone.utc),
            )


ping_service = PingService()

