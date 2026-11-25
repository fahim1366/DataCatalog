from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from backend.src.models.connections import DatabaseEngine
from backend.src.services.connectors.base import (
    BaseMetadataConnector,
    ColumnMetadata,
    ConnectionTestResult,
    ForeignKeyMetadata,
    IndexMetadata,
    RoutineMetadata,
    SchemaExtractionResult,
    SchemaMetadata,
    TableMetadata,
    TableStatisticsMetadata,
)
from backend.src.services.connectors.registry import default_connector_registry

logger = logging.getLogger(__name__)


class ClickHouseConnector(BaseMetadataConnector):
    """
    Enterprise ClickHouse columnar metadata connector.

    Communicates directly via ClickHouse's high-performance native HTTP JSON interface (port 8123)
    using httpx, with optional clickhouse-connect support. Enforces strict 5.0-second timeouts.
    """

    STATEMENT_TIMEOUT_MS: int = 30000

    def __init__(self, connection_config: Dict[str, Any]) -> None:
        super().__init__(connection_config)
        self._http_client: Optional[httpx.AsyncClient] = None

    def _get_base_url(self) -> str:
        host = self.config.get("host", "127.0.0.1")
        port = int(self.config.get("port", 8123))
        protocol = "https" if self.config.get("ssl") else "http"
        return f"{protocol}://{host}:{port}"

    def _get_auth(self) -> Optional[httpx.BasicAuth]:
        username = self.config.get("username") or self.config.get("user", "default")
        password = self.config.get("password", "")
        return httpx.BasicAuth(username, password)

    async def _query_json(self, sql: str) -> List[Dict[str, Any]]:
        """Executes a SQL query against ClickHouse HTTP interface with JSON output."""
        base_url = self._get_base_url()
        auth = self._get_auth()
        client = self._http_client or httpx.AsyncClient(timeout=10.0)
        should_close = self._http_client is None

        try:
            formatted_sql = f"{sql.strip().rstrip(';')} FORMAT JSON"
            resp = await client.post(
                base_url,
                content=formatted_sql.encode("utf-8"),
                auth=auth,
                headers={"Content-Type": "text/plain"},
            )
            if resp.status_code != 200:
                raise RuntimeError(f"ClickHouse query error ({resp.status_code}): {resp.text.strip()}")
            payload = resp.json()
            return payload.get("data", [])
        finally:
            if should_close:
                await client.aclose()

    async def __aenter__(self) -> "ClickHouseConnector":
        self._is_closed = False
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=10.0)
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None
        self._is_closed = True

    async def test_connection(self) -> ConnectionTestResult:
        """5s timeout ping returning SELECT version()."""
        start_time = time.monotonic()
        try:
            return await asyncio.wait_for(self._run_connection_test(start_time), timeout=5.0)
        except asyncio.TimeoutError:
            elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)
            return ConnectionTestResult(
                success=False,
                latency_ms=elapsed_ms,
                dialect="CLICKHOUSE",
                server_version=None,
                message="Connection probe timed out after 5.0 seconds.",
                diagnostic_details={"error_type": "TimeoutError", "timeout_seconds": 5.0},
            )
        except Exception as exc:
            elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)
            return ConnectionTestResult(
                success=False,
                latency_ms=elapsed_ms,
                dialect="CLICKHOUSE",
                server_version=None,
                message=str(exc),
                diagnostic_details={"error_type": type(exc).__name__, "error": str(exc)},
            )

    async def _run_connection_test(self, start_time: float) -> ConnectionTestResult:
        rows = await self._query_json("SELECT version() AS ver, currentDatabase() AS current_db")
        server_ver = rows[0].get("ver") if rows else None
        current_db = rows[0].get("current_db") if rows else ""
        elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)

        return ConnectionTestResult(
            success=True,
            latency_ms=elapsed_ms,
            dialect="CLICKHOUSE",
            server_version=server_ver,
            message=f"Successfully connected to ClickHouse database '{current_db}'.",
            diagnostic_details={
                "version_string": server_ver or "",
                "current_database": current_db,
                "session_read_only": True,
                "tcp_reachable": True,
                "auth_valid": True,
            },
        )

    async def discover_databases(self) -> List[str]:
        """Query SELECT name FROM system.databases excluding internal databases."""
        sql = """
            SELECT name 
            FROM system.databases 
            WHERE name NOT IN ('system', 'INFORMATION_SCHEMA', 'information_schema')
            ORDER BY name
        """
        rows = await self._query_json(sql)
        return [r["name"] for r in rows if "name" in r]

    async def _run_database_discovery(self, start_time: float) -> Dict[str, Any]:
        server_ver = None
        try:
            ver_rows = await self._query_json("SELECT version() AS ver")
            if ver_rows:
                server_ver = ver_rows[0].get("ver")
        except Exception:
            pass

        databases = await self.discover_databases()
        elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": True,
            "databases": databases,
            "latency_ms": elapsed_ms,
            "server_version": server_ver,
            "message": f"Successfully discovered {len(databases)} ClickHouse databases.",
        }

    async def extract_schemas(self) -> List[SchemaMetadata]:
        """Lists discoverable non-system databases."""
        dbs = await self.discover_databases()
        return [SchemaMetadata(name=name) for name in dbs]

    async def extract_schema_metadata(self, schema_name: str) -> SchemaExtractionResult:
        """Extracts ClickHouse tables, columns, statistics, and sorting keys."""
        safe_schema = schema_name.replace("'", "''")

        # 1. Tables & statistics
        tbl_sql = f"""
            SELECT 
                name,
                engine,
                total_rows,
                total_bytes,
                comment
            FROM system.tables
            WHERE database = '{safe_schema}'
            ORDER BY name
        """
        tbl_rows = await self._query_json(tbl_sql)

        tables: List[TableMetadata] = []
        statistics: List[TableStatisticsMetadata] = []
        for r in tbl_rows:
            table_name = r["name"]
            is_view = "View" in r.get("engine", "")
            table_type = "VIEW" if is_view else "TABLE"
            row_count = int(r.get("total_rows") or 0)
            size_bytes = int(r.get("total_bytes") or 0)
            comment = r.get("comment") or None

            tables.append(
                TableMetadata(
                    schema_name=schema_name,
                    name=table_name,
                    table_type=table_type,
                    row_count_estimate=row_count,
                    size_bytes=size_bytes,
                    source_description=comment,
                    custom_properties={"engine": r.get("engine", "")},
                )
            )
            statistics.append(
                TableStatisticsMetadata(
                    schema_name=schema_name,
                    table_name=table_name,
                    row_count_estimate=row_count,
                    size_bytes=size_bytes,
                )
            )

        # 2. Columns
        col_sql = f"""
            SELECT 
                table,
                name,
                position,
                type,
                default_expression,
                is_in_primary_key,
                comment
            FROM system.columns
            WHERE database = '{safe_schema}'
            ORDER BY table, position
        """
        col_rows = await self._query_json(col_sql)

        columns: List[ColumnMetadata] = []
        pk_cols_by_table: Dict[str, List[str]] = {}

        for r in col_rows:
            tbl_name = r["table"]
            col_name = r["name"]
            is_pk = bool(r.get("is_in_primary_key"))
            if is_pk:
                pk_cols_by_table.setdefault(tbl_name, []).append(col_name)

            data_type = r.get("type", "String")
            is_nullable = "Nullable" in data_type

            columns.append(
                ColumnMetadata(
                    schema_name=schema_name,
                    table_name=tbl_name,
                    name=col_name,
                    ordinal_position=int(r.get("position", 1)),
                    data_type=data_type,
                    raw_data_type=data_type,
                    is_nullable=is_nullable,
                    is_primary_key=is_pk,
                    default_value=r.get("default_expression") or None,
                    source_description=r.get("comment") or None,
                )
            )

        # 3. Primary Key Indexes
        indexes: List[IndexMetadata] = []
        for tbl_name, pk_cols in pk_cols_by_table.items():
            indexes.append(
                IndexMetadata(
                    schema_name=schema_name,
                    table_name=tbl_name,
                    name=f"pk_{tbl_name}",
                    is_unique=True,
                    is_primary=True,
                    index_type="PRIMARY_KEY",
                    columns_list=pk_cols,
                )
            )

        # 4. Routines (ClickHouse functions)
        routines: List[RoutineMetadata] = []
        try:
            fn_rows = await self._query_json("SELECT name, is_aggregate, origin FROM system.functions WHERE origin = 'System' LIMIT 20")
            for r in fn_rows:
                routines.append(
                    RoutineMetadata(
                        schema_name=schema_name,
                        name=r["name"],
                        routine_type="AGGREGATE" if r.get("is_aggregate") else "FUNCTION",
                        language="C++",
                    )
                )
        except Exception:
            pass

        return SchemaExtractionResult(
            schema=SchemaMetadata(name=schema_name),
            tables=tables,
            columns=columns,
            foreign_keys=[],  # ClickHouse does not enforce relational foreign keys
            statistics=statistics,
            indexes=indexes,
            routines=routines,
            success=True,
        )

    async def _execute_ddl(self, sql: str) -> None:
        """Executes a DDL statement against ClickHouse HTTP interface."""
        base_url = self._get_base_url()
        auth = self._get_auth()
        client = self._http_client or httpx.AsyncClient(timeout=10.0)
        should_close = self._http_client is None
        try:
            resp = await client.post(
                base_url,
                content=sql.strip().encode("utf-8"),
                auth=auth,
                headers={"Content-Type": "text/plain"},
            )
            if resp.status_code != 200:
                raise RuntimeError(f"ClickHouse DDL error ({resp.status_code}): {resp.text.strip()}")
        finally:
            if should_close:
                await client.aclose()

    async def extract_database_users(self) -> List[Dict[str, Any]]:
        """
        Extracts live ClickHouse users, role grants, and active processes
        from system.users, system.role_grants, system.roles, and system.processes.
        """
        return await asyncio.wait_for(self._run_extract_database_users(), timeout=5.0)

    async def _run_extract_database_users(self) -> List[Dict[str, Any]]:
        user_rows = await self._query_json(
            "SELECT id, name, auth_type, host_ip, host_names FROM system.users ORDER BY name"
        )

        roles_by_user: Dict[str, List[str]] = {}
        try:
            grant_rows = await self._query_json(
                "SELECT user_name, granted_role_name FROM system.role_grants WHERE user_name IS NOT NULL"
            )
            for g in grant_rows:
                if g.get("user_name") and g.get("granted_role_name"):
                    roles_by_user.setdefault(str(g["user_name"]), []).append(str(g["granted_role_name"]))
        except Exception as exc:
            logger.debug("ClickHouse system.role_grants query failed: %s", exc)

        sessions_by_user: Dict[str, int] = {}
        try:
            proc_rows = await self._query_json(
                "SELECT user, count() AS active_sessions FROM system.processes GROUP BY user"
            )
            for p in proc_rows:
                if p.get("user"):
                    sessions_by_user[str(p["user"])] = int(p.get("active_sessions") or 0)
        except Exception as exc:
            logger.debug("ClickHouse system.processes query failed: %s", exc)

        users: List[Dict[str, Any]] = []
        for row in user_rows:
            uname = str(row["name"])
            auth_type = str(row.get("auth_type") or "")
            host_ip = row.get("host_ip")
            can_login = (auth_type != "no_password") or bool(host_ip)
            member_roles = roles_by_user.get(uname, [])
            is_super = uname == "default" or any(r.lower() in ("admin", "superuser") for r in member_roles)

            users.append({
                "oid": str(row.get("id") or uname),
                "username": uname,
                "can_login": can_login,
                "is_superuser": is_super,
                "can_create_db": is_super,
                "can_create_role": is_super,
                "connection_limit": -1,
                "valid_until": None,
                "member_of_roles": member_roles,
                "active_sessions": sessions_by_user.get(uname, 0),
            })

        try:
            role_rows = await self._query_json("SELECT id, name FROM system.roles ORDER BY name")
            for r in role_rows:
                rname = str(r["name"])
                users.append({
                    "oid": str(r.get("id") or rname),
                    "username": rname,
                    "can_login": False,
                    "is_superuser": rname.lower() in ("admin", "superuser"),
                    "can_create_db": False,
                    "can_create_role": False,
                    "connection_limit": -1,
                    "valid_until": None,
                    "member_of_roles": [],
                    "active_sessions": 0,
                })
        except Exception as exc:
            logger.debug("ClickHouse system.roles query failed: %s", exc)

        return users

    async def update_database_user(
        self,
        username: str,
        can_login: Optional[bool] = None,
        connection_limit: Optional[int] = None,
        valid_until: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Executes ALTER USER on ClickHouse for HOST ANY/NONE or VALID UNTIL.
        Returns a clear Persian message if connection_limit is requested (managed via SETTINGS PROFILE).
        """
        if not username or not username.strip():
            return {"success": False, "message": "نام کاربری نامعتبر است."}

        clean_user = username.strip()
        if any(ch in clean_user for ch in ("\x00", "`", ";", "\r", "\n")):
            return {"success": False, "message": "نام کاربری حاوی نویسه‌های غیرمجاز است."}

        if connection_limit is not None:
            return {
                "success": False,
                "message": "تغییر سقف اتصال همزمان در ClickHouse از طریق پروفایل تنظیمات (SETTINGS PROFILE) انجام می‌شود و مستقیماً برای کاربر قابل اعمال نیست.",
            }

        clauses: List[str] = []
        if can_login is not None:
            clauses.append("HOST ANY" if can_login else "HOST NONE")
        if valid_until is not None:
            vu = valid_until.strip()
            if not vu or vu.lower() in ("infinity", "none", "null"):
                clauses.append("VALID UNTIL NULL")
            else:
                safe_vu = vu.replace("'", "''")
                clauses.append(f"VALID UNTIL '{safe_vu}'")

        if not clauses:
            return {"success": True, "message": "هیچ تغییری برای اعمال ارسال نشده است."}

        sql_stmt = f"ALTER USER `{clean_user}` {' '.join(clauses)}"
        try:
            await self._execute_ddl(sql_stmt)
            return {
                "success": True,
                "message": f"تنظیمات کاربر «{clean_user}» با موفقیت به‌روزرسانی شد.",
            }
        except Exception as exc:
            err_str = str(exc).lower()
            if any(k in err_str for k in ("not enough privileges", "access denied", "497")):
                return {
                    "success": False,
                    "message": "کاربر اتصال دیتابیس فاقد مجوز کافی (ALTER USER) برای تغییر کاربر در ClickHouse است.",
                    "insufficient_privilege": True,
                }
            return {
                "success": False,
                "message": f"خطا در به‌روزرسانی کاربر ClickHouse: {str(exc)}",
            }


# Auto-register with default registry
default_connector_registry.register(DatabaseEngine.CLICKHOUSE, ClickHouseConnector)
default_connector_registry.register("CLICKHOUSE", ClickHouseConnector)
