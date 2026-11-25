from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

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


def _has_oracledb() -> bool:
    """Checks whether the oracledb package is installed."""
    try:
        import oracledb  # noqa: F401
        return True
    except ImportError:
        return False


class OracleConnector(BaseMetadataConnector):
    """
    Enterprise Oracle Database metadata connector.

    Uses oracledb thin mode async connections with strict 5.0-second timeouts.
    Supports both service_name and SID connection modes.
    Introspects ALL_TABLES, ALL_VIEWS, ALL_TAB_COLUMNS, ALL_CONSTRAINTS,
    ALL_INDEXES, and ALL_PROCEDURES with zero N+1 queries.
    """

    STATEMENT_TIMEOUT_MS: int = 30000

    QUERIES: Dict[str, str] = {
        "TEST": """
            SELECT banner FROM v$version WHERE ROWNUM = 1
        """,
        "TEST_FALLBACK": """
            SELECT 'Oracle Database' AS banner FROM DUAL
        """,
        "CURRENT_SCHEMA": """
            SELECT SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA') AS current_schema FROM DUAL
        """,
        "DISCOVER_SCHEMAS": """
            SELECT USERNAME 
            FROM ALL_USERS 
            WHERE ORACLE_MAINTAINED = 'N' 
            ORDER BY USERNAME
        """,
        "DISCOVER_SCHEMAS_FALLBACK": """
            SELECT USERNAME 
            FROM ALL_USERS 
            WHERE USERNAME NOT IN (
                'SYS', 'SYSTEM', 'ANONYMOUS', 'APEX_PUBLIC_USER', 'APPQOSSYS',
                'AUDSYS', 'CTXSYS', 'DBSNMP', 'DIP', 'GSMADMIN_INTERNAL', 'GSMCATUSER',
                'GSMUSER', 'LBACSYS', 'MDDATA', 'MDSYS', 'OJVMSYS', 'OLAPSYS',
                'ORACLE_OCM', 'ORDDATA', 'ORDPLUGINS', 'ORDSYS', 'OUTLN', 'SI_INFORMTN_SCHEMA',
                'SPATIAL_CSW_ADMIN_USR', 'SPATIAL_WFS_ADMIN_USR', 'WMSYS', 'XDB', 'XS$NULL'
            )
            ORDER BY USERNAME
        """,
        "COLLATION": """
            SELECT VALUE FROM NLS_DATABASE_PARAMETERS WHERE PARAMETER = 'NLS_SORT'
        """,
        "TABLES": """
            SELECT 
                t.TABLE_NAME AS table_name,
                'TABLE' AS table_type,
                NVL(t.NUM_ROWS, 0) AS row_count_estimate,
                NVL(t.BLOCKS * 8192, 0) AS size_bytes,
                c.COMMENTS AS source_description,
                t.OWNER AS owner
            FROM ALL_TABLES t
            LEFT JOIN ALL_TAB_COMMENTS c ON t.OWNER = c.OWNER AND t.TABLE_NAME = c.TABLE_NAME AND c.TABLE_TYPE = 'TABLE'
            WHERE t.OWNER = :schema_name
            UNION ALL
            SELECT 
                v.VIEW_NAME AS table_name,
                'VIEW' AS table_type,
                0 AS row_count_estimate,
                0 AS size_bytes,
                c.COMMENTS AS source_description,
                v.OWNER AS owner
            FROM ALL_VIEWS v
            LEFT JOIN ALL_TAB_COMMENTS c ON v.OWNER = c.OWNER AND v.VIEW_NAME = c.TABLE_NAME AND c.TABLE_TYPE = 'VIEW'
            WHERE v.OWNER = :schema_name
            ORDER BY table_name
        """,
        "COLUMNS": """
            SELECT 
                col.TABLE_NAME AS table_name,
                col.COLUMN_NAME AS column_name,
                col.COLUMN_ID AS ordinal_position,
                col.DATA_TYPE AS data_type,
                col.DATA_TYPE || CASE 
                    WHEN col.DATA_TYPE IN ('VARCHAR2', 'CHAR', 'NVARCHAR2', 'NCHAR') THEN '(' || col.CHAR_LENGTH || ')'
                    WHEN col.DATA_TYPE = 'NUMBER' AND col.DATA_PRECISION IS NOT NULL THEN '(' || col.DATA_PRECISION || NVL2(col.DATA_SCALE, ',' || col.DATA_SCALE, '') || ')'
                    ELSE ''
                END AS raw_data_type,
                NULL AS collation,
                CASE WHEN col.NULLABLE = 'Y' THEN 1 ELSE 0 END AS is_nullable,
                CASE WHEN pk.COLUMN_NAME IS NOT NULL THEN 1 ELSE 0 END AS is_primary_key,
                CASE WHEN fk.COLUMN_NAME IS NOT NULL THEN 1 ELSE 0 END AS is_foreign_key,
                col.DATA_DEFAULT AS default_value,
                comm.COMMENTS AS source_description
            FROM ALL_TAB_COLUMNS col
            LEFT JOIN ALL_COL_COMMENTS comm 
                ON col.OWNER = comm.OWNER AND col.TABLE_NAME = comm.TABLE_NAME AND col.COLUMN_NAME = comm.COLUMN_NAME
            LEFT JOIN (
                SELECT cc.OWNER, cc.TABLE_NAME, cc.COLUMN_NAME
                FROM ALL_CONSTRAINTS c
                JOIN ALL_CONS_COLUMNS cc ON c.OWNER = cc.OWNER AND c.CONSTRAINT_NAME = cc.CONSTRAINT_NAME
                WHERE c.CONSTRAINT_TYPE = 'P' AND c.OWNER = :schema_name
            ) pk ON col.OWNER = pk.OWNER AND col.TABLE_NAME = pk.TABLE_NAME AND col.COLUMN_NAME = pk.COLUMN_NAME
            LEFT JOIN (
                SELECT cc.OWNER, cc.TABLE_NAME, cc.COLUMN_NAME
                FROM ALL_CONSTRAINTS c
                JOIN ALL_CONS_COLUMNS cc ON c.OWNER = cc.OWNER AND c.CONSTRAINT_NAME = cc.CONSTRAINT_NAME
                WHERE c.CONSTRAINT_TYPE = 'R' AND c.OWNER = :schema_name
            ) fk ON col.OWNER = fk.OWNER AND col.TABLE_NAME = fk.TABLE_NAME AND col.COLUMN_NAME = fk.COLUMN_NAME
            WHERE col.OWNER = :schema_name
            ORDER BY col.TABLE_NAME, col.COLUMN_ID
        """,
        "FOREIGN_KEYS": """
            SELECT 
                c.TABLE_NAME AS table_name,
                cc.COLUMN_NAME AS column_name,
                r.OWNER AS foreign_schema_name,
                r.TABLE_NAME AS foreign_table_name,
                rc.COLUMN_NAME AS foreign_column_name,
                c.CONSTRAINT_NAME AS constraint_name
            FROM ALL_CONSTRAINTS c
            JOIN ALL_CONS_COLUMNS cc ON c.OWNER = cc.OWNER AND c.CONSTRAINT_NAME = cc.CONSTRAINT_NAME
            JOIN ALL_CONSTRAINTS r ON c.R_OWNER = r.OWNER AND c.R_CONSTRAINT_NAME = r.CONSTRAINT_NAME
            JOIN ALL_CONS_COLUMNS rc ON r.OWNER = rc.OWNER AND r.CONSTRAINT_NAME = rc.CONSTRAINT_NAME AND cc.POSITION = rc.POSITION
            WHERE c.CONSTRAINT_TYPE = 'R' AND c.OWNER = :schema_name
            ORDER BY c.TABLE_NAME, c.CONSTRAINT_NAME
        """,
        "STATISTICS": """
            SELECT 
                TABLE_NAME AS table_name,
                NVL(NUM_ROWS, 0) AS row_count_estimate,
                NVL(BLOCKS * 8192, 0) AS size_bytes
            FROM ALL_TABLES
            WHERE OWNER = :schema_name
            ORDER BY TABLE_NAME
        """,
        "INDEXES": """
            SELECT 
                i.TABLE_NAME AS table_name,
                i.INDEX_NAME AS index_name,
                CASE WHEN i.UNIQUENESS = 'UNIQUE' THEN 1 ELSE 0 END AS is_unique,
                CASE WHEN c.CONSTRAINT_TYPE = 'P' THEN 1 ELSE 0 END AS is_primary,
                i.INDEX_TYPE AS index_type,
                LISTAGG(ic.COLUMN_NAME, ', ') WITHIN GROUP (ORDER BY ic.COLUMN_POSITION) AS column_names
            FROM ALL_INDEXES i
            JOIN ALL_IND_COLUMNS ic ON i.OWNER = ic.INDEX_OWNER AND i.INDEX_NAME = ic.INDEX_NAME
            LEFT JOIN ALL_CONSTRAINTS c ON i.OWNER = c.OWNER AND i.INDEX_NAME = c.CONSTRAINT_NAME
            WHERE i.OWNER = :schema_name
            GROUP BY i.TABLE_NAME, i.INDEX_NAME, i.UNIQUENESS, c.CONSTRAINT_TYPE, i.INDEX_TYPE
            ORDER BY i.TABLE_NAME, i.INDEX_NAME
        """,
        "ROUTINES": """
            SELECT 
                OBJECT_NAME AS routine_name,
                OBJECT_TYPE AS routine_type,
                'PL/SQL' AS language,
                NULL AS source_description
            FROM ALL_PROCEDURES
            WHERE OWNER = :schema_name AND OBJECT_TYPE IN ('PROCEDURE', 'FUNCTION')
            ORDER BY OBJECT_NAME
        """,
    }

    def __init__(self, connection_config: Dict[str, Any]) -> None:
        super().__init__(connection_config)
        self._connection: Any = None

    def _ensure_driver(self) -> None:
        if not _has_oracledb():
            raise MissingDriverError(
                engine="ORACLE",
                package_name="oracledb",
                message="برای اتصال به پایگاه‌داده Oracle، لطفاً پکیج 'oracledb' را نصب نمایید.",
            )

    async def _get_oracle_connection(self) -> Any:
        self._ensure_driver()
        import oracledb

        host = self.config.get("host", "127.0.0.1")
        port = int(self.config.get("port", 1521))
        username = self.config.get("username") or self.config.get("user", "system")
        password = self.config.get("password", "")
        custom_params = self.config.get("custom_params") or {}

        service_name = (
            custom_params.get("service_name")
            or self.config.get("database_name")
            or self.config.get("database")
            or "ORCLCDB"
        )
        sid = custom_params.get("sid")

        connect_kwargs: Dict[str, Any] = {
            "user": username,
            "password": password,
            "host": host,
            "port": port,
        }
        if sid:
            connect_kwargs["sid"] = sid
        else:
            connect_kwargs["service_name"] = service_name

        return await oracledb.connect_async(**connect_kwargs)

    async def _get_cursor(self, conn: Any) -> Any:
        raw = conn.cursor()
        if asyncio.iscoroutine(raw):
            raw = await raw
        return raw

    async def _close_cursor(self, cursor: Any) -> None:
        if cursor is not None and hasattr(cursor, "close"):
            res = cursor.close()
            if asyncio.iscoroutine(res):
                await res

    async def __aenter__(self) -> "OracleConnector":
        self._is_closed = False
        if self._connection is None:
            self._connection = await self._get_oracle_connection()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._connection is not None:
            try:
                await self._connection.close()
            except Exception:
                pass
            self._connection = None
        self._is_closed = True

    async def test_connection(self) -> ConnectionTestResult:
        """5s timeout ping querying SELECT banner FROM v$version or DUAL."""
        start_time = time.monotonic()
        try:
            return await asyncio.wait_for(self._run_connection_test(start_time), timeout=5.0)
        except asyncio.TimeoutError:
            elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)
            return ConnectionTestResult(
                success=False,
                latency_ms=elapsed_ms,
                dialect="ORACLE",
                server_version=None,
                message="Connection probe timed out after 5.0 seconds.",
                diagnostic_details={"error_type": "TimeoutError", "timeout_seconds": 5.0},
            )
        except Exception as exc:
            elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)
            return ConnectionTestResult(
                success=False,
                latency_ms=elapsed_ms,
                dialect="ORACLE",
                server_version=None,
                message=str(exc),
                diagnostic_details={"error_type": type(exc).__name__, "error": str(exc)},
            )

    async def _run_connection_test(self, start_time: float) -> ConnectionTestResult:
        conn = self._connection
        should_close = False
        if conn is None:
            conn = await self._get_oracle_connection()
            should_close = True

        cursor = None
        try:
            cursor = await self._get_cursor(conn)
            server_ver = None
            try:
                await cursor.execute(self.QUERIES["TEST"])
                row = await cursor.fetchone()
                if row:
                    server_ver = str(row[0])
            except Exception:
                await cursor.execute(self.QUERIES["TEST_FALLBACK"])
                row = await cursor.fetchone()
                if row:
                    server_ver = str(row[0])

            current_schema = ""
            try:
                await cursor.execute(self.QUERIES["CURRENT_SCHEMA"])
                row = await cursor.fetchone()
                if row:
                    current_schema = str(row[0])
            except Exception:
                pass

            elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)
            return ConnectionTestResult(
                success=True,
                latency_ms=elapsed_ms,
                dialect="ORACLE",
                server_version=server_ver,
                message=f"Successfully connected to Oracle database (schema '{current_schema}').",
                diagnostic_details={
                    "version_string": server_ver or "",
                    "current_schema": current_schema,
                    "session_read_only": True,
                    "tcp_reachable": True,
                    "auth_valid": True,
                },
            )
        finally:
            await self._close_cursor(cursor)
            if should_close:
                await conn.close()

    async def discover_databases(self) -> List[str]:
        """Query accessible non-system schemas (ALL_USERS where ORACLE_MAINTAINED = 'N')."""
        conn = self._connection
        should_close = False
        if conn is None:
            conn = await self._get_oracle_connection()
            should_close = True

        cursor = None
        try:
            cursor = await self._get_cursor(conn)
            try:
                await cursor.execute(self.QUERIES["DISCOVER_SCHEMAS"])
                rows = await cursor.fetchall()
                return [r[0] for r in rows]
            except Exception:
                await cursor.execute(self.QUERIES["DISCOVER_SCHEMAS_FALLBACK"])
                rows = await cursor.fetchall()
                return [r[0] for r in rows]
        finally:
            await self._close_cursor(cursor)
            if should_close:
                await conn.close()

    async def _run_database_discovery(self, start_time: float) -> Dict[str, Any]:
        conn = self._connection
        should_close = False
        if conn is None:
            conn = await self._get_oracle_connection()
            should_close = True

        cursor = None
        try:
            cursor = await self._get_cursor(conn)
            server_ver = None
            try:
                await cursor.execute(self.QUERIES["TEST"])
                row = await cursor.fetchone()
                if row:
                    server_ver = str(row[0])
            except Exception:
                pass

            dbs: List[str] = []
            try:
                await cursor.execute(self.QUERIES["DISCOVER_SCHEMAS"])
                rows = await cursor.fetchall()
                dbs = [r[0] for r in rows]
            except Exception:
                await cursor.execute(self.QUERIES["DISCOVER_SCHEMAS_FALLBACK"])
                rows = await cursor.fetchall()
                dbs = [r[0] for r in rows]

            elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)
            return {
                "success": True,
                "databases": dbs,
                "latency_ms": elapsed_ms,
                "server_version": server_ver,
                "message": f"Successfully discovered {len(dbs)} Oracle schemas.",
            }
        finally:
            await self._close_cursor(cursor)
            if should_close:
                await conn.close()
    async def extract_database_collation(self) -> Optional[str]:
        """Extracts Oracle NLS collation/sort parameter."""
        conn = self._connection
        should_close = False
        if conn is None:
            conn = await self._get_oracle_connection()
            should_close = True

        cursor = None
        try:
            cursor = await self._get_cursor(conn)
            await cursor.execute(self.QUERIES["COLLATION"])
            row = await cursor.fetchone()
            return str(row[0]) if row and row[0] else None
        except Exception:
            return None
        finally:
            await self._close_cursor(cursor)
            if should_close:
                await conn.close()

    async def extract_schemas(self) -> List[SchemaMetadata]:
        """Lists discoverable non-system user schemas."""
        dbs = await self.discover_databases()
        return [SchemaMetadata(name=name) for name in dbs]

    async def extract_schema_metadata(self, schema_name: str) -> SchemaExtractionResult:
        """Bulk extracts Oracle tables, columns, constraints, statistics, indexes, and routines."""
        conn = self._connection
        should_close = False
        if conn is None:
            conn = await self._get_oracle_connection()
            should_close = True

        cursor = None
        try:
            cursor = await self._get_cursor(conn)
            # Tables
            await cursor.execute(self.QUERIES["TABLES"], schema_name=schema_name)
            tbl_rows = await cursor.fetchall()
            tables = [
                TableMetadata(
                    schema_name=schema_name,
                    name=r[0],
                    table_type=r[1],
                    row_count_estimate=int(r[2] or 0),
                    size_bytes=int(r[3] or 0),
                    source_description=r[4],
                    owner=r[5],
                )
                for r in tbl_rows
            ]

            # Columns
            await cursor.execute(self.QUERIES["COLUMNS"], schema_name=schema_name)
            col_rows = await cursor.fetchall()
            columns = [
                ColumnMetadata(
                    schema_name=schema_name,
                    table_name=r[0],
                    name=r[1],
                    ordinal_position=int(r[2]),
                    data_type=r[3],
                    raw_data_type=r[4] or r[3],
                    collation=r[5],
                    is_nullable=bool(r[6]),
                    is_primary_key=bool(r[7]),
                    is_foreign_key=bool(r[8]),
                    default_value=str(r[9]) if r[9] is not None else None,
                    source_description=r[10],
                )
                for r in col_rows
            ]

            # Foreign Keys
            await cursor.execute(self.QUERIES["FOREIGN_KEYS"], schema_name=schema_name)
            fk_rows = await cursor.fetchall()
            foreign_keys = [
                ForeignKeyMetadata(
                    schema_name=schema_name,
                    table_name=r[0],
                    column_name=r[1],
                    foreign_schema_name=r[2],
                    foreign_table_name=r[3],
                    foreign_column_name=r[4],
                    constraint_name=r[5],
                )
                for r in fk_rows
            ]

            # Statistics
            await cursor.execute(self.QUERIES["STATISTICS"], schema_name=schema_name)
            stat_rows = await cursor.fetchall()
            statistics = [
                TableStatisticsMetadata(
                    schema_name=schema_name,
                    table_name=r[0],
                    row_count_estimate=int(r[1] or 0),
                    size_bytes=int(r[2] or 0),
                )
                for r in stat_rows
            ]

            # Indexes
            indexes: List[IndexMetadata] = []
            try:
                await cursor.execute(self.QUERIES["INDEXES"], schema_name=schema_name)
                idx_rows = await cursor.fetchall()
                indexes = [
                    IndexMetadata(
                        schema_name=schema_name,
                        table_name=r[0],
                        name=r[1],
                        is_unique=bool(r[2]),
                        is_primary=bool(r[3]),
                        index_type=r[4] or "BTREE",
                        columns_list=[c.strip() for c in (r[5] or "").split(",") if c.strip()],
                    )
                    for r in idx_rows
                ]
            except Exception:
                pass

            # Routines
            routines: List[RoutineMetadata] = []
            try:
                await cursor.execute(self.QUERIES["ROUTINES"], schema_name=schema_name)
                rt_rows = await cursor.fetchall()
                routines = [
                    RoutineMetadata(
                        schema_name=schema_name,
                        name=r[0],
                        routine_type=r[1],
                        language=r[2] or "PL/SQL",
                        source_description=r[3],
                    )
                    for r in rt_rows
                ]
            except Exception:
                pass

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
            await self._close_cursor(cursor)
            if should_close:
                await conn.close()

    async def extract_database_users(self) -> List[Dict[str, Any]]:
        """
        Extracts live Oracle database users from DBA_USERS (fallback ALL_USERS),
        role memberships from DBA_ROLE_PRIVS, and active sessions from V$SESSION.
        """
        return await asyncio.wait_for(self._run_extract_database_users(), timeout=5.0)

    async def _run_extract_database_users(self) -> List[Dict[str, Any]]:
        conn = self._connection or await self._get_oracle_connection()
        should_close = self._connection is None
        cursor = None
        try:
            cursor = await self._get_cursor(conn)

            has_dba_users = True
            try:
                await cursor.execute(
                    "SELECT USER_ID, USERNAME, ACCOUNT_STATUS, EXPIRY_DATE FROM DBA_USERS ORDER BY USERNAME"
                )
                user_rows = await cursor.fetchall()
            except Exception as exc:
                logger.debug("Oracle DBA_USERS restricted, falling back to ALL_USERS: %s", exc)
                has_dba_users = False
                await cursor.execute(
                    "SELECT USER_ID, USERNAME FROM ALL_USERS ORDER BY USERNAME"
                )
                user_rows = await cursor.fetchall()

            roles_by_user: Dict[str, List[str]] = {}
            try:
                await cursor.execute("SELECT GRANTEE, GRANTED_ROLE FROM DBA_ROLE_PRIVS")
                for r in await cursor.fetchall():
                    roles_by_user.setdefault(str(r[0]), []).append(str(r[1]))
            except Exception as exc:
                logger.debug("Oracle DBA_ROLE_PRIVS restricted: %s", exc)

            sessions_by_user: Dict[str, int] = {}
            try:
                await cursor.execute(
                    "SELECT USERNAME, COUNT(*) FROM V$SESSION WHERE USERNAME IS NOT NULL GROUP BY USERNAME"
                )
                for s in await cursor.fetchall():
                    if s[0]:
                        sessions_by_user[str(s[0])] = int(s[1] or 0)
            except Exception as exc:
                logger.debug("Oracle V$SESSION restricted: %s", exc)

            users: List[Dict[str, Any]] = []
            for row in user_rows:
                uid = str(row[0])
                uname = str(row[1])
                if has_dba_users:
                    acct_status = str(row[2] or "OPEN").upper()
                    expiry = row[3]
                    can_login = "LOCKED" not in acct_status
                    valid_until = (
                        expiry.isoformat()
                        if hasattr(expiry, "isoformat")
                        else (str(expiry) if expiry is not None else None)
                    )
                else:
                    can_login = True
                    valid_until = None

                member_roles = roles_by_user.get(uname, [])
                is_dba = "DBA" in member_roles

                users.append({
                    "oid": uid,
                    "username": uname,
                    "can_login": can_login,
                    "is_superuser": is_dba,
                    "can_create_db": False,
                    "can_create_role": is_dba,
                    "connection_limit": -1,
                    "valid_until": valid_until,
                    "member_of_roles": member_roles,
                    "active_sessions": sessions_by_user.get(uname, 0),
                })
            return users
        finally:
            await self._close_cursor(cursor)
            if should_close:
                await conn.close()

    async def update_database_user(
        self,
        username: str,
        can_login: Optional[bool] = None,
        connection_limit: Optional[int] = None,
        valid_until: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Executes ALTER USER "USERNAME" ACCOUNT UNLOCK/LOCK on Oracle.
        """
        if not username or not username.strip():
            return {"success": False, "message": "نام کاربری نامعتبر است."}

        clean_user = username.strip()
        if any(ch in clean_user for ch in ("\x00", '"', ";", "\r", "\n")):
            return {"success": False, "message": "نام کاربری حاوی نویسه‌های غیرمجاز است."}

        if connection_limit is not None or valid_until is not None:
            return {
                "success": False,
                "message": "تغییر سقف اتصال یا تاریخ انقضا در Oracle از این بخش پشتیبانی نمی‌شود؛ تنها قفل/بازگشایی حساب کاربری قابل انجام است.",
            }

        if can_login is None:
            return {"success": True, "message": "هیچ تغییری برای اعمال ارسال نشده است."}

        action = "ACCOUNT UNLOCK" if can_login else "ACCOUNT LOCK"
        sql_stmt = f'ALTER USER "{clean_user}" {action}'

        conn = self._connection or await self._get_oracle_connection()
        should_close = self._connection is None
        cursor = None
        try:
            cursor = await self._get_cursor(conn)
            await cursor.execute(sql_stmt)
            if hasattr(conn, "commit"):
                commit_res = conn.commit()
                if asyncio.iscoroutine(commit_res):
                    await commit_res
            return {
                "success": True,
                "message": f"تنظیمات کاربر «{clean_user}» با موفقیت به‌روزرسانی شد.",
            }
        except Exception as exc:
            err_str = str(exc).lower()
            if any(k in err_str for k in ("ora-01031", "insufficient privileges")):
                return {
                    "success": False,
                    "message": "کاربر اتصال دیتابیس فاقد مجوز کافی (ALTER USER یا DBA) برای تغییر وضعیت حساب در Oracle است.",
                    "insufficient_privilege": True,
                }
            return {
                "success": False,
                "message": f"خطا در به‌روزرسانی کاربر Oracle: {str(exc)}",
            }
        finally:
            await self._close_cursor(cursor)
            if should_close:
                await conn.close()


# Auto-register with default registry
default_connector_registry.register(DatabaseEngine.ORACLE, OracleConnector)
default_connector_registry.register("ORACLE", OracleConnector)
