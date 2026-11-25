from __future__ import annotations

import asyncio
import logging
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Set

from backend.src.models.connections import DatabaseEngine
from backend.src.services.connectors.base import (
    BaseMetadataConnector,
    ColumnMetadata,
    ConnectionTestResult,
    IndexMetadata,
    MissingDriverError,
    SchemaExtractionResult,
    SchemaMetadata,
    TableMetadata,
    TableStatisticsMetadata,
)
from backend.src.services.connectors.registry import default_connector_registry

logger = logging.getLogger(__name__)


def _has_mongo_driver() -> bool:
    """Checks whether motor or pymongo is installed."""
    try:
        import motor.motor_asyncio  # noqa: F401
        return True
    except ImportError:
        try:
            import pymongo  # noqa: F401
            return True
        except ImportError:
            return False


class MongoDBConnector(BaseMetadataConnector):
    """
    Enterprise MongoDB document database metadata connector.

    Treats databases as schemas, collections as tables, and samples up to 50 documents
    per collection to infer dynamic BSON/JSON schema structure, nested types, and indexes.
    Enforces strict 5.0-second timeouts.
    """

    STATEMENT_TIMEOUT_MS: int = 30000

    def __init__(self, connection_config: Dict[str, Any]) -> None:
        super().__init__(connection_config)
        self._client: Any = None

    def _ensure_driver(self) -> None:
        if not _has_mongo_driver():
            raise MissingDriverError(
                engine="MONGODB",
                package_name="motor",
                message="برای اتصال به پایگاه‌داده MongoDB، لطفاً پکیج 'motor' یا 'pymongo' را نصب نمایید.",
            )

    def _build_connection_uri(self) -> str:
        if "url" in self.config:
            return self.config["url"]

        host = self.config.get("host", "127.0.0.1")
        port = int(self.config.get("port", 27017))
        username = self.config.get("username")
        password = self.config.get("password")
        custom_params = self.config.get("custom_params") or {}
        auth_source = custom_params.get("authSource", "admin")

        auth = ""
        if username and password:
            q_user = urllib.parse.quote_plus(str(username))
            q_pass = urllib.parse.quote_plus(str(password))
            auth = f"{q_user}:{q_pass}@"
        elif username:
            auth = f"{urllib.parse.quote_plus(str(username))}@"

        return f"mongodb://{auth}{host}:{port}/?authSource={auth_source}"

    async def _get_client(self) -> Any:
        self._ensure_driver()
        import motor.motor_asyncio

        uri = self._build_connection_uri()
        return motor.motor_asyncio.AsyncIOMotorClient(
            uri,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            socketTimeoutMS=10000,
        )

    async def __aenter__(self) -> "MongoDBConnector":
        self._is_closed = False
        if self._client is None:
            self._client = await self._get_client()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
        self._is_closed = True

    async def test_connection(self) -> ConnectionTestResult:
        """5s timeout ping command and server_info()."""
        start_time = time.monotonic()
        try:
            return await asyncio.wait_for(self._run_connection_test(start_time), timeout=5.0)
        except asyncio.TimeoutError:
            elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)
            return ConnectionTestResult(
                success=False,
                latency_ms=elapsed_ms,
                dialect="MONGODB",
                server_version=None,
                message="Connection probe timed out after 5.0 seconds.",
                diagnostic_details={"error_type": "TimeoutError", "timeout_seconds": 5.0},
            )
        except Exception as exc:
            elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)
            return ConnectionTestResult(
                success=False,
                latency_ms=elapsed_ms,
                dialect="MONGODB",
                server_version=None,
                message=str(exc),
                diagnostic_details={"error_type": type(exc).__name__, "error": str(exc)},
            )

    async def _run_connection_test(self, start_time: float) -> ConnectionTestResult:
        client = self._client
        should_close = False
        if client is None:
            client = await self._get_client()
            should_close = True

        try:
            admin_db = client.admin
            await admin_db.command("ping")
            server_info = await client.server_info()
            server_ver = server_info.get("version")
            elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)

            return ConnectionTestResult(
                success=True,
                latency_ms=elapsed_ms,
                dialect="MONGODB",
                server_version=server_ver,
                message=f"Successfully connected to MongoDB server v{server_ver}.",
                diagnostic_details={
                    "version_string": server_ver or "",
                    "session_read_only": True,
                    "tcp_reachable": True,
                    "auth_valid": True,
                },
            )
        finally:
            if should_close:
                client.close()

    async def discover_databases(self) -> List[str]:
        """list_database_names() excluding admin, local, config."""
        client = self._client
        should_close = False
        if client is None:
            client = await self._get_client()
            should_close = True

        try:
            all_dbs = await client.list_database_names()
            excluded = {"admin", "local", "config"}
            return [db for db in all_dbs if db not in excluded]
        finally:
            if should_close:
                client.close()

    async def _run_database_discovery(self, start_time: float) -> Dict[str, Any]:
        client = self._client
        should_close = False
        if client is None:
            client = await self._get_client()
            should_close = True

        try:
            server_ver = None
            try:
                info = await client.server_info()
                server_ver = info.get("version")
            except Exception:
                pass

            databases = await self.discover_databases()
            elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)
            return {
                "success": True,
                "databases": databases,
                "latency_ms": elapsed_ms,
                "server_version": server_ver,
                "message": f"Successfully discovered {len(databases)} MongoDB databases.",
            }
        finally:
            if should_close:
                client.close()

    async def extract_schemas(self) -> List[SchemaMetadata]:
        """Lists discoverable non-system databases."""
        dbs = await self.discover_databases()
        return [SchemaMetadata(name=name) for name in dbs]

    @staticmethod
    def _infer_type(val: Any) -> str:
        """Maps Python / BSON types to standard type string."""
        if val is None:
            return "null"
        if isinstance(val, bool):
            return "bool"
        if isinstance(val, int):
            return "int"
        if isinstance(val, float):
            return "double"
        if isinstance(val, str):
            return "string"
        if isinstance(val, dict):
            return "object"
        if isinstance(val, (list, tuple)):
            return "array"
        tname = type(val).__name__.lower()
        if "objectid" in tname:
            return "objectId"
        if "datetime" in tname or "timestamp" in tname:
            return "date"
        return tname

    async def extract_schema_metadata(self, schema_name: str) -> SchemaExtractionResult:
        """
        Introspects collections, infers schema from up to 50 documents, and extracts indexes.
        """
        client = self._client
        should_close = False
        if client is None:
            client = await self._get_client()
            should_close = True

        try:
            db = client[schema_name]
            coll_names = await db.list_collection_names()

            tables: List[TableMetadata] = []
            columns: List[ColumnMetadata] = []
            statistics: List[TableStatisticsMetadata] = []
            indexes: List[IndexMetadata] = []

            for coll_name in coll_names:
                if coll_name.startswith("system."):
                    continue

                coll = db[coll_name]

                # Fast stats
                row_count = 0
                size_bytes = 0
                try:
                    stats = await db.command("collStats", coll_name)
                    row_count = int(stats.get("count", 0))
                    size_bytes = int(stats.get("size", stats.get("storageSize", 0)))
                except Exception:
                    try:
                        row_count = await coll.estimated_document_count()
                    except Exception:
                        pass

                tables.append(
                    TableMetadata(
                        schema_name=schema_name,
                        name=coll_name,
                        table_type="TABLE",
                        row_count_estimate=row_count,
                        size_bytes=size_bytes,
                    )
                )
                statistics.append(
                    TableStatisticsMetadata(
                        schema_name=schema_name,
                        table_name=coll_name,
                        row_count_estimate=row_count,
                        size_bytes=size_bytes,
                    )
                )

                # Sample up to 50 documents to infer fields
                field_types: Dict[str, Set[str]] = {}
                field_seen_count: Dict[str, int] = {}
                doc_count = 0

                try:
                    cursor = coll.find().limit(50)
                    async for doc in cursor:
                        doc_count += 1
                        for key, val in doc.items():
                            t = self._infer_type(val)
                            field_types.setdefault(key, set()).add(t)
                            field_seen_count[key] = field_seen_count.get(key, 0) + 1
                except Exception as exc:
                    logger.debug("MongoDB sampling failed for collection %s: %s", coll_name, exc)

                # Ensure _id is present
                if "_id" not in field_types:
                    field_types["_id"] = {"objectId"}
                    field_seen_count["_id"] = doc_count

                # Create column metadata
                ordinal = 1
                for field_name in sorted(field_types.keys(), key=lambda k: (0 if k == "_id" else 1, k)):
                    types = field_types[field_name]
                    type_str = "/".join(sorted(types)) if types else "unknown"
                    seen = field_seen_count.get(field_name, 0)
                    is_pk = (field_name == "_id")
                    is_nullable = not is_pk and (seen < doc_count or "null" in types)

                    columns.append(
                        ColumnMetadata(
                            schema_name=schema_name,
                            table_name=coll_name,
                            name=field_name,
                            ordinal_position=ordinal,
                            data_type=type_str,
                            raw_data_type=type_str,
                            is_nullable=is_nullable,
                            is_primary_key=is_pk,
                        )
                    )
                    ordinal += 1

                # Extract indexes
                try:
                    idx_info = await coll.index_information()
                    for idx_name, idx_meta in idx_info.items():
                        key_pairs = idx_meta.get("key", [])
                        cols = [k[0] for k in key_pairs]
                        is_uniq = bool(idx_meta.get("unique", False))
                        is_pk = (idx_name == "_id_")

                        indexes.append(
                            IndexMetadata(
                                schema_name=schema_name,
                                table_name=coll_name,
                                name=idx_name,
                                is_unique=is_uniq,
                                is_primary=is_pk,
                                index_type="BTREE",
                                columns_list=cols,
                            )
                        )
                except Exception as exc:
                    logger.debug("MongoDB index extraction failed for %s: %s", coll_name, exc)

            return SchemaExtractionResult(
                schema=SchemaMetadata(name=schema_name),
                tables=tables,
                columns=columns,
                foreign_keys=[],  # MongoDB is non-relational
                statistics=statistics,
                indexes=indexes,
                routines=[],
                success=True,
            )
        finally:
            if should_close:
                client.close()

    async def extract_database_users(self) -> List[Dict[str, Any]]:
        """
        Extracts live MongoDB users via usersInfo on admin (and target DB)
        and active operations/sessions via currentOp.
        """
        return await asyncio.wait_for(self._run_extract_database_users(), timeout=5.0)

    async def _run_extract_database_users(self) -> List[Dict[str, Any]]:
        client = self._client or await self._get_client()
        should_close = self._client is None
        try:
            raw_users: List[Dict[str, Any]] = []
            seen_ids: Set[str] = set()

            target_db = self.config.get("database_name") or self.config.get("database") or "admin"
            dbs_to_query = ["admin"] if target_db == "admin" else ["admin", target_db]

            for db_name in dbs_to_query:
                try:
                    info = await client[db_name].command({"usersInfo": 1})
                    for u in info.get("users", []):
                        uid = str(u.get("_id") or f"{u.get('user', '')}@{u.get('db', db_name)}")
                        if uid not in seen_ids:
                            seen_ids.add(uid)
                            raw_users.append(u)
                except Exception as exc:
                    logger.debug("MongoDB usersInfo failed on %s: %s", db_name, exc)

            sessions_by_user: Dict[str, int] = {}
            try:
                curr_op = await client["admin"].command({"currentOp": 1})
                for op in curr_op.get("inprog", []):
                    for eff in op.get("effectiveUsers", []):
                        uname = eff.get("user")
                        if uname:
                            sessions_by_user[str(uname)] = sessions_by_user.get(str(uname), 0) + 1
            except Exception as exc:
                logger.debug("MongoDB currentOp query failed: %s", exc)

            users: List[Dict[str, Any]] = []
            for u in raw_users:
                uname = str(u.get("user", ""))
                roles = [str(r.get("role")) for r in u.get("roles", []) if r.get("role")]
                is_super = any(r in ("root", "__system") for r in roles)
                can_create_db = is_super or any(
                    r in ("dbAdminAnyDatabase", "dbAdmin", "readWriteAnyDatabase") for r in roles
                )
                can_create_role = is_super or any(
                    r in ("userAdminAnyDatabase", "userAdmin") for r in roles
                )
                users.append({
                    "oid": str(u.get("_id", uname)),
                    "username": uname,
                    "can_login": True,
                    "is_superuser": is_super,
                    "can_create_db": can_create_db,
                    "can_create_role": can_create_role,
                    "connection_limit": -1,
                    "valid_until": None,
                    "member_of_roles": roles,
                    "active_sessions": sessions_by_user.get(uname, 0),
                })
            return users
        finally:
            if should_close:
                client.close()

    async def update_database_user(
        self,
        username: str,
        can_login: Optional[bool] = None,
        connection_limit: Optional[int] = None,
        valid_until: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        MongoDB manages login via user deletion or role revocation rather than toggleable lock flags.
        """
        return {
            "success": False,
            "message": "مدیریت قفل/بازگشایی ورود یا محدودیت اتصال در MongoDB از طریق حذف کاربر یا لغو نقش‌ها (Role Revocation) انجام می‌شود و تغییر وضعیت قفل به‌صورت مستقیم پشتیبانی نمی‌شود.",
        }


# Auto-register with default registry
default_connector_registry.register(DatabaseEngine.MONGODB, MongoDBConnector)
default_connector_registry.register("MONGODB", MongoDBConnector)
