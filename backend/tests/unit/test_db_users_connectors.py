from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from backend.src.services.connectors.clickhouse import ClickHouseConnector
from backend.src.services.connectors.mongodb import MongoDBConnector
from backend.src.services.connectors.mssql import MSSQLConnector
from backend.src.services.connectors.mysql import MySQLConnector
from backend.src.services.connectors.oracle import OracleConnector


def _make_async_engine_mock(results_by_query_substring):
    mock_conn = AsyncMock()

    async def mock_execute(stmt):
        sql = str(stmt)
        res = MagicMock()
        for substr, rows in results_by_query_substring.items():
            if substr in sql:
                res.mappings.return_value.all.return_value = rows
                return res
        res.mappings.return_value.all.return_value = []
        return res

    mock_conn.execute = mock_execute
    mock_engine = MagicMock()
    mock_engine.connect.return_value.__aenter__.return_value = mock_conn
    mock_engine.connect.return_value.__aexit__.return_value = None
    mock_engine.dispose = AsyncMock()
    return mock_engine


@pytest.mark.asyncio
async def test_mssql_extract_database_users():
    connector = MSSQLConnector({"host": "127.0.0.1", "port": 1433, "database_name": "master"})
    mock_engine = _make_async_engine_mock({
        "sys.server_role_members": [
            {"member_name": "sa", "role_name": "sysadmin"},
        ],
        "sys.dm_exec_sessions": [
            {"username": "sa", "active_sessions": 2},
        ],
        "sys.server_principals": [
            {
                "oid": 1,
                "username": "sa",
                "principal_type": "S",
                "is_disabled": 0,
                "is_superuser": 1,
                "can_create_db": 1,
                "can_create_role": 1,
            },
            {
                "oid": 2,
                "username": "db_readonly_role",
                "principal_type": "R",
                "is_disabled": 0,
                "is_superuser": 0,
                "can_create_db": 0,
                "can_create_role": 0,
            },
        ],
    })

    with patch.object(connector, "_create_engine", return_value=mock_engine):
        users = await connector.extract_database_users()

    assert len(users) == 2
    assert users[0]["username"] == "sa"
    assert users[0]["can_login"] is True
    assert users[0]["is_superuser"] is True
    assert users[0]["member_of_roles"] == ["sysadmin"]
    assert users[0]["active_sessions"] == 2
    assert users[1]["username"] == "db_readonly_role"
    assert users[1]["can_login"] is False


@pytest.mark.asyncio
async def test_mysql_extract_database_users():
    connector = MySQLConnector({"host": "127.0.0.1", "port": 3306, "database_name": "app"})
    mock_engine = _make_async_engine_mock({
        "mysql.user": [
            {
                "User": "app_user",
                "Host": "%",
                "account_locked": "N",
                "Super_priv": "N",
                "Create_db_priv": "Y",
                "Create_user_priv": "N",
                "max_user_connections": 25,
                "password_lifetime": 90,
            },
        ],
        "mysql.role_edges": [
            {"FROM_USER": "reader_role", "FROM_HOST": "%", "TO_USER": "app_user", "TO_HOST": "%"},
        ],
        "information_schema.PROCESSLIST": [
            {"USER": "app_user", "active_sessions": 4},
        ],
    })

    with patch.object(connector, "_create_engine", return_value=mock_engine):
        users = await connector.extract_database_users()

    assert len(users) == 1
    assert users[0]["username"] == "app_user"
    assert users[0]["can_login"] is True
    assert users[0]["can_create_db"] is True
    assert users[0]["connection_limit"] == 25
    assert users[0]["valid_until"] == "90"
    assert users[0]["member_of_roles"] == ["reader_role"]
    assert users[0]["active_sessions"] == 4


@pytest.mark.asyncio
async def test_oracle_extract_database_users():
    connector = OracleConnector({"host": "127.0.0.1", "port": 1521, "database_name": "ORCL"})
    mock_cursor = AsyncMock()
    queries = []

    async def mock_execute(sql):
        queries.append(sql)

    async def mock_fetchall():
        last = queries[-1]
        if "DBA_USERS" in last:
            return [(101, "APP_OWNER", "OPEN", datetime(2027, 1, 1, 0, 0, 0))]
        if "DBA_ROLE_PRIVS" in last:
            return [("APP_OWNER", "DBA"), ("APP_OWNER", "CONNECT")]
        if "V$SESSION" in last:
            return [("APP_OWNER", 3)]
        return []

    mock_cursor.execute = mock_execute
    mock_cursor.fetchall = mock_fetchall

    mock_conn = AsyncMock()
    mock_conn.close = AsyncMock()

    with patch.object(connector, "_get_oracle_connection", return_value=mock_conn), \
         patch.object(connector, "_get_cursor", return_value=mock_cursor), \
         patch.object(connector, "_close_cursor", new_callable=AsyncMock):
        users = await connector.extract_database_users()

    assert len(users) == 1
    assert users[0]["oid"] == "101"
    assert users[0]["username"] == "APP_OWNER"
    assert users[0]["can_login"] is True
    assert users[0]["is_superuser"] is True
    assert users[0]["member_of_roles"] == ["DBA", "CONNECT"]
    assert users[0]["active_sessions"] == 3


@pytest.mark.asyncio
async def test_clickhouse_extract_database_users():
    connector = ClickHouseConnector({"host": "127.0.0.1", "port": 8123, "database_name": "default"})

    async def mock_query_json(sql):
        if "system.users" in sql:
            return [{"id": "u1", "name": "default", "auth_type": "sha256_password", "host_ip": ["::/0"]}]
        if "system.role_grants" in sql:
            return [{"user_name": "default", "granted_role_name": "admin"}]
        if "system.processes" in sql:
            return [{"user": "default", "active_sessions": 1}]
        if "system.roles" in sql:
            return [{"id": "r1", "name": "readonly_role"}]
        return []

    with patch.object(connector, "_query_json", side_effect=mock_query_json):
        users = await connector.extract_database_users()

    assert len(users) == 2
    assert users[0]["username"] == "default"
    assert users[0]["can_login"] is True
    assert users[0]["is_superuser"] is True
    assert users[0]["member_of_roles"] == ["admin"]
    assert users[0]["active_sessions"] == 1
    assert users[1]["username"] == "readonly_role"
    assert users[1]["can_login"] is False


@pytest.mark.asyncio
async def test_mongodb_extract_database_users():
    connector = MongoDBConnector({"host": "127.0.0.1", "port": 27017, "database_name": "analytics"})

    mock_admin_db = MagicMock()

    async def mock_admin_command(cmd):
        if "usersInfo" in cmd:
            return {
                "users": [
                    {
                        "_id": "admin.root",
                        "user": "root",
                        "db": "admin",
                        "roles": [{"role": "root", "db": "admin"}],
                    }
                ]
            }
        if "currentOp" in cmd:
            return {"inprog": [{"effectiveUsers": [{"user": "root"}]}]}
        return {}

    mock_admin_db.command = mock_admin_command

    mock_analytics_db = MagicMock()
    mock_analytics_db.command = AsyncMock(return_value={"users": []})

    mock_client = MagicMock()
    mock_client.__getitem__.side_effect = lambda name: mock_admin_db if name == "admin" else mock_analytics_db
    mock_client.close = MagicMock()

    with patch.object(connector, "_get_client", return_value=mock_client):
        users = await connector.extract_database_users()

    assert len(users) == 1
    assert users[0]["username"] == "root"
    assert users[0]["is_superuser"] is True
    assert users[0]["member_of_roles"] == ["root"]
    assert users[0]["active_sessions"] == 1
