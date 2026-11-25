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


def _detect_mysql_driver() -> Optional[str]:
    """Detects available async MySQL drivers."""
    try:
        import aiomysql  # noqa: F401
        return "aiomysql"
    except ImportError:
        pass
    try:
        import asyncmy  # noqa: F401
        return "asyncmy"
    except ImportError:
        pass
    return None


class MySQLConnector(BaseMetadataConnector):
    """
    Enterprise MySQL metadata connector.

    Uses ephemeral connections with NullPool, strict timeouts, and read-only session controls.
    Gracefully detects and falls back between aiomysql and asyncmy.
    """

    STATEMENT_TIMEOUT_MS: int = 30000

    QUERIES: Dict[str, str] = {
        "TEST": """
            SELECT VERSION() AS server_version, DATABASE() AS current_database;
        """,
        "DISCOVER_DATABASES": """
            SELECT SCHEMA_NAME 
            FROM INFORMATION_SCHEMA.SCHEMATA 
            WHERE SCHEMA_NAME NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')
            ORDER BY SCHEMA_NAME;
        """,
        "COLLATION": """
            SELECT DEFAULT_COLLATION_NAME 
            FROM INFORMATION_SCHEMA.SCHEMATA 
            WHERE SCHEMA_NAME = DATABASE();
        """,
        "SCHEMAS": """
            SELECT 
                SCHEMA_NAME AS schema_name, 
                DEFAULT_COLLATION_NAME AS collation_name 
            FROM INFORMATION_SCHEMA.SCHEMATA 
            WHERE SCHEMA_NAME NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')
            ORDER BY SCHEMA_NAME;
        """,
        "TABLES": """
            SELECT 
                TABLE_NAME AS table_name,
                CASE TABLE_TYPE WHEN 'VIEW' THEN 'VIEW' ELSE 'TABLE' END AS table_type,
                COALESCE(TABLE_ROWS, 0) AS row_count_estimate,
                COALESCE(DATA_LENGTH + INDEX_LENGTH, 0) AS size_bytes,
                TABLE_COMMENT AS source_description
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = :schema_name
            ORDER BY TABLE_NAME;
        """,
        "COLUMNS": """
            SELECT 
                TABLE_NAME AS table_name,
                COLUMN_NAME AS column_name,
                ORDINAL_POSITION AS ordinal_position,
                DATA_TYPE AS data_type,
                COLUMN_TYPE AS raw_data_type,
                COLLATION_NAME AS collation,
                CASE WHEN IS_NULLABLE = 'YES' THEN 1 ELSE 0 END AS is_nullable,
                CASE WHEN COLUMN_KEY = 'PRI' THEN 1 ELSE 0 END AS is_primary_key,
                CASE WHEN COLUMN_KEY = 'MUL' THEN 1 ELSE 0 END AS is_foreign_key,
                COLUMN_DEFAULT AS default_value,
                COLUMN_COMMENT AS source_description
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = :schema_name
            ORDER BY TABLE_NAME, ORDINAL_POSITION;
        """,
        "FOREIGN_KEYS": """
            SELECT 
                kcu.TABLE_NAME AS table_name,
                kcu.COLUMN_NAME AS column_name,
                kcu.REFERENCED_TABLE_SCHEMA AS foreign_schema_name,
                kcu.REFERENCED_TABLE_NAME AS foreign_table_name,
                kcu.REFERENCED_COLUMN_NAME AS foreign_column_name,
                kcu.CONSTRAINT_NAME AS constraint_name
            FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
            WHERE kcu.TABLE_SCHEMA = :schema_name 
              AND kcu.REFERENCED_TABLE_NAME IS NOT NULL
            ORDER BY kcu.TABLE_NAME, kcu.CONSTRAINT_NAME;
        """,
        "STATISTICS": """
            SELECT 
                TABLE_NAME AS table_name,
                COALESCE(TABLE_ROWS, 0) AS row_count_estimate,
                COALESCE(DATA_LENGTH + INDEX_LENGTH, 0) AS size_bytes
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = :schema_name
            ORDER BY TABLE_NAME;
        """,
        "INDEXES": """
            SELECT 
                TABLE_NAME AS table_name,
                INDEX_NAME AS index_name,
                CASE WHEN NON_UNIQUE = 0 THEN 1 ELSE 0 END AS is_unique,
                CASE WHEN INDEX_NAME = 'PRIMARY' THEN 1 ELSE 0 END AS is_primary,
                INDEX_TYPE AS index_type,
                GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX SEPARATOR ', ') AS column_names
            FROM INFORMATION_SCHEMA.STATISTICS
            WHERE TABLE_SCHEMA = :schema_name
            GROUP BY TABLE_NAME, INDEX_NAME, NON_UNIQUE, INDEX_TYPE
            ORDER BY TABLE_NAME, INDEX_NAME;
        """,
        "ROUTINES": """
            SELECT 
                ROUTINE_NAME AS routine_name,
                ROUTINE_TYPE AS routine_type,
                ROUTINE_BODY AS language,
                ROUTINE_COMMENT AS source_description,
                DTD_IDENTIFIER AS return_type
            FROM INFORMATION_SCHEMA.ROUTINES
            WHERE ROUTINE_SCHEMA = :schema_name
            ORDER BY ROUTINE_NAME;
        """,
    }

    def __init__(self, connection_config: Dict[str, Any]) -> None:
        super().__init__(connection_config)
        self._engine: Optional[AsyncEngine] = None

    def get_pool_class(self) -> Type[Pool]:
        return NullPool

    def get_session_setup_statements(self) -> List[str]:
        return [
            "SET SESSION TRANSACTION READ ONLY;",
            f"SET SESSION max_execution_time = {self.STATEMENT_TIMEOUT_MS};",
        ]

    def build_connection_url(self) -> str:
        """Constructs an async driver connection URL."""
        if "url" in self.config:
            return self.config["url"]

        driver = _detect_mysql_driver()
        if not driver:
            raise MissingDriverError(
                engine="MYSQL",
                package_name="aiomysql",
                message="برای اتصال به پایگاه‌داده MySQL، لطفاً پکیج 'aiomysql' یا 'asyncmy' را نصب نمایید.",
            )

        host = self.config.get("host", "127.0.0.1")
        port = self.config.get("port", 3306)
        database = self.config.get("database") or self.config.get("database_name", "information_schema")
        username = self.config.get("username") or self.config.get("user", "root")
        password = self.config.get("password", "")

        quoted_username = urllib.parse.quote_plus(str(username))
        quoted_password = urllib.parse.quote_plus(str(password)) if password else ""
        auth = f"{quoted_username}:{quoted_password}@" if quoted_password else f"{quoted_username}@"

        return f"mysql+{driver}://{auth}{host}:{port}/{database}"

    def _create_engine(self) -> AsyncEngine:
        url = self.build_connection_url()
        return create_async_engine(
            url,
            poolclass=self.get_pool_class(),
            connect_args={"connect_timeout": 10},
            echo=False,
        )

    async def __aenter__(self) -> "MySQLConnector":
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
        """5s timeout ping returning SELECT VERSION()."""
        start_time = time.monotonic()
        try:
            return await asyncio.wait_for(self._run_connection_test(start_time), timeout=5.0)
        except asyncio.TimeoutError:
            elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)
            return ConnectionTestResult(
                success=False,
                latency_ms=elapsed_ms,
                dialect="MYSQL",
                server_version=None,
                message="Connection probe timed out after 5.0 seconds.",
                diagnostic_details={"error_type": "TimeoutError", "timeout_seconds": 5.0},
            )
        except Exception as exc:
            elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)
            return ConnectionTestResult(
                success=False,
                latency_ms=elapsed_ms,
                dialect="MYSQL",
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
                    try:
                        await conn.execute(text(stmt))
                    except Exception:
                        pass  # Some older MySQL engines do not support max_execution_time

                result = await conn.execute(text(self.QUERIES["TEST"]))
                row = result.fetchone()
                server_ver = row[0] if row else None
                db_name = row[1] if row else ""
                elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)

                return ConnectionTestResult(
                    success=True,
                    latency_ms=elapsed_ms,
                    dialect="MYSQL",
                    server_version=server_ver,
                    message=f"Successfully connected to MySQL database '{db_name}'.",
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
        """Query INFORMATION_SCHEMA.SCHEMATA excluding system schemas."""
        engine = self._engine or self._create_engine()
        should_dispose = self._engine is None
        try:
            async with engine.connect() as conn:
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
        """Extracts MySQL database default collation."""
        engine = self._engine or self._create_engine()
        should_dispose = self._engine is None
        try:
            async with engine.connect() as conn:
                result = await conn.execute(text(self.QUERIES["COLLATION"]))
                row = result.fetchone()
                return str(row[0]) if row and row[0] else None
        except Exception as exc:
            logger.debug("MySQL database collation extraction failed: %s", exc)
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
                result = await conn.execute(text(self.QUERIES["SCHEMAS"]))
                return [
                    SchemaMetadata(name=row["schema_name"])
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
                    try:
                        await conn.execute(text(stmt))
                    except Exception:
                        pass

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

    async def extract_database_users(self) -> List[Dict[str, Any]]:
        """
        Extracts live MySQL users, privileges, role edges, and active sessions
        from mysql.user, mysql.role_edges, and information_schema.PROCESSLIST.
        """
        return await asyncio.wait_for(self._run_extract_database_users(), timeout=15.0)

    async def _run_extract_database_users(self) -> List[Dict[str, Any]]:
        engine = self._engine or self._create_engine()
        should_dispose = self._engine is None
        try:
            async with engine.connect() as conn:
                for stmt in self.get_session_setup_statements():
                    await conn.execute(text(stmt))

                users_sql = """
                    SELECT
                        User,
                        Host,
                        account_locked,
                        Super_priv,
                        Create_db_priv,
                        Create_user_priv,
                        max_user_connections,
                        password_lifetime
                    FROM mysql.user
                    ORDER BY User, Host;
                """
                res = await conn.execute(text(users_sql))
                rows = res.mappings().all()

                roles_by_target: Dict[tuple[str, str], List[str]] = {}
                try:
                    role_sql = """
                        SELECT FROM_USER, FROM_HOST, TO_USER, TO_HOST
                        FROM mysql.role_edges;
                    """
                    role_res = await conn.execute(text(role_sql))
                    for r in role_res.mappings().all():
                        key = (str(r["TO_USER"]), str(r["TO_HOST"]))
                        from_u = str(r["FROM_USER"])
                        from_h = str(r["FROM_HOST"])
                        role_label = f"{from_u}@{from_h}" if from_h and from_h != "%" else from_u
                        roles_by_target.setdefault(key, []).append(role_label)
                except Exception as exc:
                    logger.debug("MySQL role_edges query failed: %s", exc)

                sessions_by_user: Dict[str, int] = {}
                try:
                    proc_sql = """
                        SELECT USER, COUNT(*) AS active_sessions
                        FROM information_schema.PROCESSLIST
                        GROUP BY USER;
                    """
                    proc_res = await conn.execute(text(proc_sql))
                    for p in proc_res.mappings().all():
                        if p["USER"]:
                            sessions_by_user[str(p["USER"])] = int(p["active_sessions"] or 0)
                except Exception as exc:
                    logger.debug("MySQL PROCESSLIST query failed: %s", exc)

                users: List[Dict[str, Any]] = []
                for row in rows:
                    user_name = str(row["User"])
                    host_name = str(row["Host"])
                    formatted_name = f"{user_name}@{host_name}" if host_name and host_name != "%" else user_name
                    max_conn = row.get("max_user_connections")
                    conn_limit = int(max_conn) if max_conn is not None and int(max_conn) > 0 else -1
                    pwd_lifetime = row.get("password_lifetime")

                    users.append({
                        "oid": f"{user_name}@{host_name}",
                        "username": formatted_name,
                        "can_login": str(row.get("account_locked") or "N").upper() != "Y",
                        "is_superuser": str(row.get("Super_priv") or "N").upper() == "Y",
                        "can_create_db": str(row.get("Create_db_priv") or "N").upper() == "Y",
                        "can_create_role": str(row.get("Create_user_priv") or "N").upper() == "Y",
                        "connection_limit": conn_limit,
                        "valid_until": str(pwd_lifetime) if pwd_lifetime is not None else None,
                        "member_of_roles": roles_by_target.get((user_name, host_name), []),
                        "active_sessions": sessions_by_user.get(user_name, 0),
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
        Executes ALTER USER 'user'@'host' ACCOUNT LOCK/UNLOCK and/or WITH MAX_USER_CONNECTIONS N.
        """
        if not username or not username.strip():
            return {"success": False, "message": "نام کاربری نامعتبر است."}

        clean_user = username.strip()
        if any(ch in clean_user for ch in ("\x00", ";", "\r", "\n")):
            return {"success": False, "message": "نام کاربری حاوی نویسه‌های غیرمجاز است."}

        if "@" in clean_user:
            user_part, host_part = clean_user.rsplit("@", 1)
        else:
            user_part, host_part = clean_user, "%"

        if not user_part or not host_part:
            return {"success": False, "message": "فرمت نام کاربری MySQL نامعتبر است."}

        safe_user = user_part.replace("'", "''")
        safe_host = host_part.replace("'", "''")

        clauses: List[str] = []
        if can_login is not None:
            clauses.append("ACCOUNT UNLOCK" if can_login else "ACCOUNT LOCK")
        if connection_limit is not None:
            limit_val = 0 if int(connection_limit) < 0 else int(connection_limit)
            clauses.append(f"WITH MAX_USER_CONNECTIONS {limit_val}")

        if not clauses:
            if valid_until is not None:
                return {
                    "success": False,
                    "message": "تغییر تاریخ انقضای مشخص در MySQL پشتیبانی نمی‌شود.",
                }
            return {"success": True, "message": "هیچ تغییری برای اعمال ارسال نشده است."}

        sql_stmt = f"ALTER USER '{safe_user}'@'{safe_host}' {' '.join(clauses)};"

        engine = self._engine or self._create_engine()
        should_dispose = self._engine is None
        try:
            async with engine.connect() as conn:
                await conn.execute(text(f"SET SESSION max_execution_time = {self.STATEMENT_TIMEOUT_MS};"))
                await conn.execute(text(sql_stmt))
                await conn.commit()
                return {
                    "success": True,
                    "message": f"تنظیمات کاربر «{clean_user}» با موفقیت به‌روزرسانی شد.",
                }
        except Exception as exc:
            err_str = str(exc).lower()
            if any(k in err_str for k in ("access denied", "1227", "create user")):
                return {
                    "success": False,
                    "message": "کاربر اتصال دیتابیس فاقد مجوز کافی (CREATE USER یا SYSTEM_USER) برای تغییر کاربر در MySQL است.",
                    "insufficient_privilege": True,
                }
            return {
                "success": False,
                "message": f"خطا در به‌روزرسانی کاربر MySQL: {str(exc)}",
            }
        finally:
            if should_dispose:
                await engine.dispose()


# Auto-register with default registry
default_connector_registry.register(DatabaseEngine.MYSQL, MySQLConnector)
default_connector_registry.register("MYSQL", MySQLConnector)
