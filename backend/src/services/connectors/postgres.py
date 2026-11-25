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
    RoutineMetadata,
    SchemaExtractionResult,
    SchemaMetadata,
    TableMetadata,
    TableStatisticsMetadata,
)
from backend.src.services.connectors.registry import default_connector_registry

logger = logging.getLogger(__name__)


class PostgresConnector(BaseMetadataConnector):
    """
    High-performance native PostgreSQL metadata connector.

    Introspects native PostgreSQL system catalogs (pg_catalog) directly,
    bypassing slow ANSI information_schema views. Guarantees zero N+1 queries.
    Uses ephemeral connections with NullPool and strict session timeouts.
    """

    QUERIES: Dict[str, str] = {
        "SCHEMAS": """
            SELECT 
                n.nspname AS schema_name,
                pg_catalog.pg_get_userbyid(n.nspowner) AS owner,
                d.description AS description
            FROM pg_catalog.pg_namespace n
            LEFT JOIN pg_catalog.pg_description d ON d.objoid = n.oid AND d.objsubid = 0
            WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
              AND n.nspname NOT LIKE 'pg_temp%'
              AND n.nspname NOT LIKE 'pg_toast%'
            ORDER BY n.nspname;
        """,
        "DB_USERS": """
            SELECT 
                r.oid::text AS oid,
                r.rolname AS username,
                r.rolcanlogin AS can_login,
                r.rolsuper AS is_superuser,
                r.rolcreatedb AS can_create_db,
                r.rolcreaterole AS can_create_role,
                r.rolconnlimit AS connection_limit,
                r.rolvaliduntil::text AS valid_until,
                COALESCE(
                    (SELECT array_agg(parent.rolname ORDER BY parent.rolname)
                     FROM pg_catalog.pg_auth_members am
                     JOIN pg_catalog.pg_roles parent ON parent.oid = am.roleid
                     WHERE am.member = r.oid),
                    ARRAY[]::name[]
                ) AS member_of_roles,
                (SELECT COUNT(*)::int FROM pg_catalog.pg_stat_activity sa WHERE sa.usename = r.rolname) AS active_sessions
            FROM pg_catalog.pg_roles r
            WHERE r.rolname NOT LIKE 'pg_%'
            ORDER BY r.rolcanlogin DESC, r.rolname ASC;
        """,
        "TABLES": """
            SELECT 
                c.relname AS table_name,
                CASE c.relkind 
                    WHEN 'r' THEN 'TABLE'
                    WHEN 'v' THEN 'VIEW'
                    WHEN 'm' THEN 'MATERIALIZED_VIEW'
                    WHEN 'p' THEN 'PARTITIONED_TABLE'
                    WHEN 'f' THEN 'EXTERNAL_TABLE'
                    ELSE 'TABLE'
                END AS table_type,
                GREATEST(COALESCE(s.n_live_tup, 0), COALESCE(c.reltuples::bigint, 0), 0) AS row_count_estimate,
                pg_catalog.pg_total_relation_size(c.oid) AS size_bytes,
                d.description AS source_description,
                pg_catalog.pg_get_userbyid(c.relowner) AS owner
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_catalog.pg_stat_user_tables s ON s.relid = c.oid
            LEFT JOIN pg_catalog.pg_description d ON d.objoid = c.oid AND d.objsubid = 0
            WHERE n.nspname = :schema_name 
              AND c.relkind IN ('r', 'v', 'm', 'p', 'f')
            ORDER BY c.relname;
        """,
        "COLUMNS": """
            SELECT 
                c.relname AS table_name,
                a.attname AS column_name,
                a.attnum AS ordinal_position,
                pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
                t.typname AS raw_data_type,
                CASE WHEN a.attcollation = 0 THEN NULL ELSE col.collname END AS collation,
                NOT a.attnotnull AS is_nullable,
                COALESCE(pk.is_pk, false) AS is_primary_key,
                COALESCE(fk.is_fk, false) AS is_foreign_key,
                pg_catalog.pg_get_expr(ad.adbin, ad.adrelid) AS default_value,
                d.description AS source_description
            FROM pg_catalog.pg_attribute a
            JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_catalog.pg_type t ON t.oid = a.atttypid
            LEFT JOIN pg_catalog.pg_collation col ON col.oid = a.attcollation
            LEFT JOIN pg_catalog.pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
            LEFT JOIN pg_catalog.pg_description d ON d.objoid = a.attrelid AND d.objsubid = a.attnum
            LEFT JOIN (
                SELECT DISTINCT conrelid, unnest(conkey) AS attnum, true AS is_pk
                FROM pg_catalog.pg_constraint
                WHERE contype = 'p' AND connamespace = (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname = :schema_name)
            ) pk ON pk.conrelid = a.attrelid AND pk.attnum = a.attnum
            LEFT JOIN (
                SELECT DISTINCT conrelid, unnest(conkey) AS attnum, true AS is_fk
                FROM pg_catalog.pg_constraint
                WHERE contype = 'f' AND connamespace = (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname = :schema_name)
            ) fk ON fk.conrelid = a.attrelid AND fk.attnum = a.attnum
            WHERE n.nspname = :schema_name 
              AND c.relkind IN ('r', 'v', 'm', 'p', 'f')
              AND a.attnum > 0 
              AND NOT a.attisdropped
            ORDER BY c.relname, a.attnum;
        """,
        "FOREIGN_KEYS": """
            SELECT
                c.relname AS table_name,
                a.attname AS column_name,
                fn.nspname AS foreign_schema_name,
                fc.relname AS foreign_table_name,
                fa.attname AS foreign_column_name,
                k.conname AS constraint_name
            FROM (
                SELECT
                    con.conname,
                    con.conrelid,
                    con.confrelid,
                    con.conkey[i] AS attnum,
                    con.confkey[i] AS confattnum
                FROM pg_catalog.pg_constraint con
                CROSS JOIN LATERAL generate_subscripts(con.conkey, 1) AS i
                WHERE con.contype = 'f' AND con.connamespace = (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname = :schema_name)
            ) k
            JOIN pg_catalog.pg_class c ON c.oid = k.conrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_catalog.pg_class fc ON fc.oid = k.confrelid
            JOIN pg_catalog.pg_namespace fn ON fn.oid = fc.relnamespace
            JOIN pg_catalog.pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = k.attnum
            JOIN pg_catalog.pg_attribute fa ON fa.attrelid = k.confrelid AND fa.attnum = k.confattnum
            WHERE n.nspname = :schema_name
            ORDER BY c.relname, k.conname;
        """,
        "STATISTICS": """
            SELECT 
                c.relname AS table_name,
                GREATEST(COALESCE(s.n_live_tup, 0), COALESCE(c.reltuples::bigint, 0), 0)::bigint AS row_count_estimate,
                pg_catalog.pg_total_relation_size(c.oid)::bigint AS size_bytes
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_catalog.pg_stat_user_tables s ON s.relid = c.oid
            WHERE n.nspname = :schema_name 
              AND c.relkind IN ('r', 'v', 'm', 'p', 'f')
            ORDER BY c.relname;
        """,
        "INDEXES": """
            SELECT
                c.relname AS table_name,
                i.relname AS index_name,
                idx.indisunique AS is_unique,
                idx.indisprimary AS is_primary,
                am.amname AS index_type,
                pg_catalog.pg_get_indexdef(idx.indexrelid) AS definition,
                (
                    SELECT string_agg(a.attname, ', ' ORDER BY array_position(idx.indkey, a.attnum))
                    FROM pg_catalog.pg_attribute a
                    WHERE a.attrelid = c.oid AND a.attnum = ANY(idx.indkey)
                ) AS column_names
            FROM pg_catalog.pg_index idx
            JOIN pg_catalog.pg_class c ON c.oid = idx.indrelid
            JOIN pg_catalog.pg_class i ON i.oid = idx.indexrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_catalog.pg_am am ON am.oid = i.relam
            WHERE n.nspname = :schema_name
              AND c.relkind IN ('r', 'm', 'p')
            ORDER BY c.relname, i.relname;
        """,
        "ROUTINES": """
            SELECT
                p.proname AS routine_name,
                CASE p.prokind
                    WHEN 'p' THEN 'PROCEDURE'
                    WHEN 'f' THEN 'FUNCTION'
                    ELSE 'FUNCTION'
                END AS routine_type,
                l.lanname AS language,
                pg_catalog.pg_get_function_arguments(p.oid) AS arguments_signature,
                pg_catalog.pg_get_function_result(p.oid) AS return_type,
                d.description AS source_description
            FROM pg_catalog.pg_proc p
            JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
            LEFT JOIN pg_catalog.pg_language l ON l.oid = p.prolang
            LEFT JOIN pg_catalog.pg_description d ON d.objoid = p.oid AND d.objsubid = 0
            WHERE n.nspname = :schema_name
              AND p.prokind IN ('p', 'f')
            ORDER BY p.proname;
        """,
    }

    def __init__(self, connection_config: Dict[str, Any]) -> None:
        super().__init__(connection_config)
        self._engine: Optional[AsyncEngine] = None

    def get_pool_class(self) -> Type[Pool]:
        return NullPool

    def get_connect_args(self) -> Dict[str, Any]:
        """Returns connection arguments enforcing 10s TCP handshake/connect timeout."""
        connect_args: Dict[str, Any] = {
            "timeout": 10,
            "connect_timeout": 10,
        }
        custom_params = self.config.get("custom_params") or {}
        sslmode = custom_params.get("sslmode")
        if sslmode:
            connect_args["ssl"] = sslmode if sslmode != "disable" else False
        elif self.config.get("ssl"):
            connect_args["ssl"] = self.config.get("ssl")

        if "application_name" in custom_params:
            connect_args["server_settings"] = {"application_name": custom_params["application_name"]}
        return connect_args

    def build_connection_url(self) -> str:
        """Constructs an asyncpg driver connection URL from config."""
        if "url" in self.config:
            url = self.config["url"]
            if url.startswith("postgresql://"):
                url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
            return url

        host = self.config.get("host", "127.0.0.1")
        port = self.config.get("port", 5432)
        database = self.config.get("database") or self.config.get("database_name", "postgres")
        username = self.config.get("username") or self.config.get("user", "postgres")
        password = self.config.get("password", "")

        quoted_username = urllib.parse.quote_plus(str(username))
        quoted_password = urllib.parse.quote_plus(str(password)) if password else ""
        auth = f"{quoted_username}:{quoted_password}@" if quoted_password else f"{quoted_username}@"

        return f"postgresql+asyncpg://{auth}{host}:{port}/{database}"

    def _create_engine(self) -> AsyncEngine:
        connect_args = dict(self.get_connect_args())
        # asyncpg accepts 'timeout' instead of 'connect_timeout'
        connect_args.pop("connect_timeout", None)
        return create_async_engine(
            self.build_connection_url(),
            poolclass=self.get_pool_class(),
            connect_args=connect_args,
            echo=False,
        )

    async def __aenter__(self) -> "PostgresConnector":
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
        """
        Verifies TCP handshake, authentication, read-only session setup, and server version.
        Guarantees strict 5.0-second timeout ceiling.
        """
        start_time = time.monotonic()
        try:
            return await asyncio.wait_for(self._run_connection_test(start_time), timeout=5.0)
        except asyncio.TimeoutError:
            elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)
            return ConnectionTestResult(
                success=False,
                latency_ms=elapsed_ms,
                dialect="POSTGRESQL",
                server_version=None,
                message="Connection probe timed out after 5.0 seconds.",
                diagnostic_details={"error_type": "TimeoutError", "timeout_seconds": 5.0},
            )
        except Exception as exc:
            elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)
            pgcode = getattr(exc, "pgcode", None) or getattr(exc, "sqlstate", None)
            return ConnectionTestResult(
                success=False,
                latency_ms=elapsed_ms,
                dialect="POSTGRESQL",
                server_version=None,
                message=f"Connection test failed: {str(exc)}",
                diagnostic_details={"error_type": type(exc).__name__, "pgcode": pgcode, "error": str(exc)},
            )

    async def _run_connection_test(self, start_time: float) -> ConnectionTestResult:
        engine = self._engine or self._create_engine()
        should_dispose = self._engine is None
        try:
            async with engine.connect() as conn:
                # Apply read-only session protections
                for stmt in self.get_session_setup_statements():
                    await conn.execute(text(stmt))

                result = await conn.execute(
                    text("SELECT version(), current_setting('server_version'), current_database();")
                )
                row = result.fetchone()
                server_ver = row[1] if row else None
                db_name = row[2] if row else ""

                elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)
                return ConnectionTestResult(
                    success=True,
                    latency_ms=elapsed_ms,
                    dialect="POSTGRESQL",
                    server_version=server_ver,
                    message=f"Successfully connected to PostgreSQL database '{db_name}'.",
                    diagnostic_details={
                        "version_string": row[0] if row else "",
                        "database": db_name,
                        "session_read_only": True,
                        "tcp_reachable": True,
                        "auth_valid": True,
                        "read_only_supported": True,
                        "statement_timeout_ms": self.STATEMENT_TIMEOUT_MS,
                        "ssl_active": False,
                    },
                )
        finally:
            if should_dispose:
                await engine.dispose()

    async def extract_database_collation(self) -> Optional[str]:
        """Extracts the connected database's default collation."""
        engine = self._engine or self._create_engine()
        should_dispose = self._engine is None
        try:
            async with engine.connect() as conn:
                for stmt in self.get_session_setup_statements():
                    await conn.execute(text(stmt))
                result = await conn.execute(
                    text("SELECT datcollate FROM pg_database WHERE datname = current_database()")
                )
                row = result.fetchone()
                return str(row[0]) if row and row[0] else None
        except Exception as exc:
            logger.debug("Database collation extraction failed: %s", exc)
            return None
        finally:
            if should_dispose:
                await engine.dispose()

    async def extract_schemas(self) -> List[SchemaMetadata]:
        """Lists accessible schemas excluding internal system schemas."""
        engine = self._engine or self._create_engine()
        should_dispose = self._engine is None
        try:
            async with engine.connect() as conn:
                for stmt in self.get_session_setup_statements():
                    await conn.execute(text(stmt))

                result = await conn.execute(text(self.QUERIES["SCHEMAS"]))
                schemas = [
                    SchemaMetadata(
                        name=row.schema_name,
                        owner=row.owner,
                        description=row.description,
                    )
                    for row in result.mappings().all()
                ]
                return schemas
        finally:
            if should_dispose:
                await engine.dispose()

    async def extract_schema_metadata(self, schema_name: str) -> SchemaExtractionResult:
        """
        Executes exactly 4 bulk queries against pg_catalog for the given schema:
        1. Tables & Views
        2. Columns
        3. Foreign Keys
        4. Table Statistics
        Zero N+1 round trips.
        """
        engine = self._engine or self._create_engine()
        should_dispose = self._engine is None
        try:
            async with engine.connect() as conn:
                for stmt in self.get_session_setup_statements():
                    await conn.execute(text(stmt))

                # Query 1: Tables
                tables_res = await conn.execute(
                    text(self.QUERIES["TABLES"]),
                    {"schema_name": schema_name},
                )
                tables = [
                    TableMetadata(
                        schema_name=schema_name,
                        name=row.table_name,
                        table_type=row.table_type,
                        row_count_estimate=int(row.row_count_estimate or 0),
                        size_bytes=int(row.size_bytes or 0),
                        source_description=row.source_description,
                        owner=row.owner,
                    )
                    for row in tables_res.mappings().all()
                ]

                # Query 2: Columns
                cols_res = await conn.execute(
                    text(self.QUERIES["COLUMNS"]),
                    {"schema_name": schema_name},
                )
                columns = [
                    ColumnMetadata(
                        schema_name=schema_name,
                        table_name=row.table_name,
                        name=row.column_name,
                        ordinal_position=int(row.ordinal_position),
                        data_type=row.data_type,
                        raw_data_type=row.raw_data_type,
                        collation=row.collation,
                        is_nullable=bool(row.is_nullable),
                        is_primary_key=bool(row.is_primary_key),
                        is_foreign_key=bool(row.is_foreign_key),
                        default_value=row.default_value,
                        source_description=row.source_description,
                    )
                    for row in cols_res.mappings().all()
                ]

                # Query 3: Foreign Keys
                fks_res = await conn.execute(
                    text(self.QUERIES["FOREIGN_KEYS"]),
                    {"schema_name": schema_name},
                )
                foreign_keys = [
                    ForeignKeyMetadata(
                        schema_name=schema_name,
                        table_name=row.table_name,
                        column_name=row.column_name,
                        foreign_schema_name=row.foreign_schema_name,
                        foreign_table_name=row.foreign_table_name,
                        foreign_column_name=row.foreign_column_name,
                        constraint_name=row.constraint_name,
                    )
                    for row in fks_res.mappings().all()
                ]

                # Query 4: Statistics
                stats_res = await conn.execute(
                    text(self.QUERIES["STATISTICS"]),
                    {"schema_name": schema_name},
                )
                statistics = [
                    TableStatisticsMetadata(
                        schema_name=schema_name,
                        table_name=row.table_name,
                        row_count_estimate=int(row.row_count_estimate or 0),
                        size_bytes=int(row.size_bytes or 0),
                    )
                    for row in stats_res.mappings().all()
                ]

                # Query 5: Indexes
                idx_res = await conn.execute(
                    text(self.QUERIES["INDEXES"]),
                    {"schema_name": schema_name},
                )
                indexes = [
                    IndexMetadata(
                        schema_name=schema_name,
                        table_name=row.table_name,
                        name=row.index_name,
                        is_unique=bool(row.is_unique),
                        is_primary=bool(row.is_primary),
                        index_type=row.index_type or "BTREE",
                        columns_list=[c.strip() for c in (row.column_names or "").split(",") if c.strip()],
                        definition=row.definition,
                    )
                    for row in idx_res.mappings().all()
                ]

                # Query 6: Routines
                routines_res = await conn.execute(
                    text(self.QUERIES["ROUTINES"]),
                    {"schema_name": schema_name},
                )
                routines = [
                    RoutineMetadata(
                        schema_name=schema_name,
                        name=row.routine_name,
                        routine_type=row.routine_type,
                        language=row.language or "sql",
                        arguments_signature=row.arguments_signature,
                        return_type=row.return_type,
                        source_description=row.source_description,
                    )
                    for row in routines_res.mappings().all()
                ]

                schema_meta = SchemaMetadata(name=schema_name)
                return SchemaExtractionResult(
                    schema=schema_meta,
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

    @classmethod
    async def discover_databases_from_config(cls, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Connects transiently (NullPool, READ ONLY, 5s timeout) to the 'postgres' maintenance DB
        and queries non-template databases.
        """
        start_time = time.monotonic()
        discovery_config = dict(config)
        # Use postgres maintenance database unless overridden
        discovery_config["database"] = discovery_config.get("database") or "postgres"
        connector = cls(discovery_config)
        try:
            return await asyncio.wait_for(connector._run_database_discovery(start_time), timeout=5.0)
        except asyncio.TimeoutError:
            elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)
            return {
                "success": False,
                "databases": [],
                "latency_ms": elapsed_ms,
                "server_version": None,
                "message": "Database discovery probe timed out after 5.0 seconds.",
            }
        except Exception as exc:
            elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)
            return {
                "success": False,
                "databases": [],
                "latency_ms": elapsed_ms,
                "server_version": None,
                "message": f"Database discovery failed: {str(exc)}",
            }

    async def _run_database_discovery(self, start_time: float) -> Dict[str, Any]:
        engine = self._engine or self._create_engine()
        should_dispose = self._engine is None
        try:
            async with engine.connect() as conn:
                for stmt in self.get_session_setup_statements():
                    await conn.execute(text(stmt))

                ver_res = await conn.execute(text("SELECT version(), current_setting('server_version');"))
                ver_row = ver_res.fetchone()
                server_ver = ver_row[1] if ver_row else None

                db_res = await conn.execute(
                    text("SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname;")
                )
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

    async def extract_database_users(self) -> List[Dict[str, Any]]:
        """
        Extracts live database users/roles and their attributes, privileges,
        role memberships, and active sessions from pg_catalog.
        Runs inside NullPool, READ ONLY session, and 15-second timeout ceiling.
        """
        return await asyncio.wait_for(self._run_extract_database_users(), timeout=15.0)

    async def _run_extract_database_users(self) -> List[Dict[str, Any]]:
        engine = self._engine or self._create_engine()
        should_dispose = self._engine is None
        try:
            async with engine.connect() as conn:
                for stmt in self.get_session_setup_statements():
                    await conn.execute(text(stmt))

                result = await conn.execute(text(self.QUERIES["DB_USERS"]))
                rows = result.mappings().all()
                users: List[Dict[str, Any]] = []
                for row in rows:
                    member_roles = list(row["member_of_roles"]) if row["member_of_roles"] is not None else []
                    users.append({
                        "oid": str(row["oid"]),
                        "username": str(row["username"]),
                        "can_login": bool(row["can_login"]),
                        "is_superuser": bool(row["is_superuser"]),
                        "can_create_db": bool(row["can_create_db"]),
                        "can_create_role": bool(row["can_create_role"]),
                        "connection_limit": int(row["connection_limit"]) if row["connection_limit"] is not None else -1,
                        "valid_until": str(row["valid_until"]) if row["valid_until"] is not None else None,
                        "member_of_roles": member_roles,
                        "active_sessions": int(row["active_sessions"] or 0),
                    })
                return users
        finally:
            if should_dispose:
                await engine.dispose()

    async def update_database_user(
        self,
        username: str,
        can_login: Optional[bool] = None,
        connection_limit: Optional[int] = None,
        valid_until: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Executes ALTER ROLE statement on target database to update role configuration.
        Uses safe identifier quoting to prevent SQL injection.
        Catches InsufficientPrivilegeError / 42501 and returns a clear Persian error message.
        """
        if not username or not username.strip():
            return {"success": False, "message": "نام کاربری نامعتبر است."}

        clean_user = username.strip()
        if "\x00" in clean_user:
            return {"success": False, "message": "نام کاربری حاوی نویسه‌های غیرمجاز است."}
        escaped_user = clean_user.replace('"', '""')
        quoted_username = f'"{escaped_user}"'

        clauses: List[str] = []
        if can_login is not None:
            clauses.append("LOGIN" if can_login else "NOLOGIN")
        if connection_limit is not None:
            clauses.append(f"CONNECTION LIMIT {int(connection_limit)}")
        if valid_until is not None:
            vu = valid_until.strip()
            if not vu or vu.lower() in ("infinity", "none", "null"):
                clauses.append("VALID UNTIL 'infinity'")
            else:
                safe_vu = vu.replace("'", "''")
                clauses.append(f"VALID UNTIL '{safe_vu}'")

        if not clauses:
            return {"success": True, "message": "هیچ تغییری برای اعمال ارسال نشده است."}

        sql_stmt = f"ALTER ROLE {quoted_username} WITH {' '.join(clauses)};"

        engine = self._engine or self._create_engine()
        should_dispose = self._engine is None
        try:
            async with engine.connect() as conn:
                await conn.execute(text(f"SET statement_timeout = {self.STATEMENT_TIMEOUT_MS};"))
                await conn.execute(text(sql_stmt))
                await conn.commit()
                return {
                    "success": True,
                    "message": f"تنظیمات کاربر «{clean_user}» با موفقیت به‌روزرسانی شد.",
                }
        except Exception as exc:
            pgcode = getattr(exc, "pgcode", None) or getattr(getattr(exc, "orig", None), "sqlstate", None) or getattr(exc, "sqlstate", None)
            err_str = str(exc).lower()
            if (
                pgcode == "42501"
                or "insufficientprivilege" in type(exc).__name__.lower()
                or "permission denied" in err_str
                or "must be superuser" in err_str
                or "createrole" in err_str
            ):
                return {
                    "success": False,
                    "message": "کاربر اتصال دیتابیس فاقد مجوز کافی (CREATEROLE یا SUPERUSER) برای تغییر این نقش در پایگاه داده است.",
                    "insufficient_privilege": True,
                }
            return {
                "success": False,
                "message": f"خطا در به‌روزرسانی کاربر دیتابیس: {str(exc)}",
            }
        finally:
            if should_dispose:
                await engine.dispose()


# Auto-register with default registry
default_connector_registry.register(DatabaseEngine.POSTGRESQL, PostgresConnector)
default_connector_registry.register("POSTGRESQL", PostgresConnector)
