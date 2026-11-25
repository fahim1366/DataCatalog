from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from backend.src.api.v1.db_users import _build_connector_config
from backend.src.models.connections import DatabaseConnection, DatabaseEngine
from backend.src.services.connectors.clickhouse import ClickHouseConnector
from backend.src.services.connectors.mongodb import MongoDBConnector
from backend.src.services.connectors.mssql import MSSQLConnector
from backend.src.services.connectors.mysql import MySQLConnector
from backend.src.services.connectors.oracle import OracleConnector
from backend.src.services.connectors.postgres import PostgresConnector


# ---------------------------------------------------------------------------
# 1. API Helper: _build_connector_config
# ---------------------------------------------------------------------------

def test_build_connector_config_does_not_default_username_to_postgres():
    conn = DatabaseConnection(
        name="mssql_conn",
        engine_type=DatabaseEngine.MSSQL,
        host="10.0.0.5",
        port=1433,
        database_name="master",
        encrypted_dek=b"k" * 32,
        encrypted_credentials=b"c" * 64,
    )
    with patch("backend.src.api.v1.db_users.EnvelopeEncryptionService") as mock_crypto_cls:
        mock_crypto_cls.return_value.decrypt_credentials.return_value = {"password": "secret"}
        cfg = _build_connector_config(conn)
        assert cfg["username"] == ""
        assert cfg["password"] == "secret"


# ---------------------------------------------------------------------------
# 2. PostgreSQL
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_postgres_extract_and_update_users():
    connector = PostgresConnector({"host": "127.0.0.1", "port": 5432, "database": "postgres"})
    mock_conn = AsyncMock()
    mock_result = MagicMock()
    mock_result.mappings.return_value.all.return_value = [
        {
            "oid": 10,
            "username": "pg_admin",
            "can_login": True,
            "is_superuser": True,
            "can_create_db": True,
            "can_create_role": True,
            "connection_limit": -1,
            "valid_until": None,
            "member_of_roles": ["pg_read_all_data"],
            "active_sessions": 2,
        }
    ]
    mock_conn.execute = AsyncMock(return_value=mock_result)
    mock_conn.commit = AsyncMock()

    mock_engine = MagicMock()
    mock_engine.connect.return_value.__aenter__.return_value = mock_conn
    mock_engine.connect.return_value.__aexit__.return_value = None
    mock_engine.dispose = AsyncMock()

    with patch.object(connector, "_create_engine", return_value=mock_engine):
        users = await connector.extract_database_users()
        assert len(users) == 1
        assert users[0]["username"] == "pg_admin"
        assert users[0]["is_superuser"] is True
        assert users[0]["active_sessions"] == 2

        res = await connector.update_database_user("pg_admin", can_login=False, connection_limit=5)
        assert res["success"] is True


# ---------------------------------------------------------------------------
# 3. MSSQL
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mssql_extract_database_users():
    connector = MSSQLConnector({"host": "127.0.0.1", "port": 1433, "database": "master"})

    principals_res = MagicMock()
    principals_res.mappings.return_value.all.return_value = [
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
            "username": "CORP\\analyst",
            "principal_type": "U",
            "is_disabled": 1,
            "is_superuser": 0,
            "can_create_db": 0,
            "can_create_role": 0,
        },
        {
            "oid": 3,
            "username": "sysadmin",
            "principal_type": "R",
            "is_disabled": 0,
            "is_superuser": 0,
            "can_create_db": 0,
            "can_create_role": 0,
        },
    ]
    roles_res = MagicMock()
    roles_res.mappings.return_value.all.return_value = [
        {"member_name": "sa", "role_name": "sysadmin"},
    ]
    sess_res = MagicMock()
    sess_res.mappings.return_value.all.return_value = [
        {"username": "sa", "active_sessions": 4},
    ]

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(side_effect=[MagicMock(), MagicMock(), principals_res, roles_res, sess_res])

    mock_engine = MagicMock()
    mock_engine.connect.return_value.__aenter__.return_value = mock_conn
    mock_engine.connect.return_value.__aexit__.return_value = None
    mock_engine.dispose = AsyncMock()

    with patch.object(connector, "_create_engine", return_value=mock_engine):
        users = await connector.extract_database_users()
        assert len(users) == 3
        assert users[0]["username"] == "sa"
        assert users[0]["can_login"] is True
        assert users[0]["is_superuser"] is True
        assert users[0]["member_of_roles"] == ["sysadmin"]
        assert users[0]["active_sessions"] == 4
        assert users[1]["username"] == "CORP\\analyst"
        assert users[1]["can_login"] is False
        assert users[2]["can_login"] is False  # server role


@pytest.mark.asyncio
async def test_mssql_update_database_user():
    connector = MSSQLConnector({"host": "127.0.0.1", "port": 1433, "database": "master"})
    executed: list[str] = []

    mock_conn = AsyncMock()
    async def fake_execute(stmt):
        executed.append(str(stmt))
        return MagicMock()

    mock_conn.execute = fake_execute
    mock_conn.commit = AsyncMock()
    mock_engine = MagicMock()
    mock_engine.connect.return_value.__aenter__.return_value = mock_conn
    mock_engine.connect.return_value.__aexit__.return_value = None
    mock_engine.dispose = AsyncMock()

    with patch.object(connector, "_create_engine", return_value=mock_engine):
        res = await connector.update_database_user("CORP\\analyst", can_login=False)
        assert res["success"] is True
        assert any("ALTER LOGIN [CORP\\analyst] DISABLE;" in s for s in executed)

        # Injection attempt rejected
        bad = await connector.update_database_user("sa]; DROP TABLE x;--", can_login=False)
        assert bad["success"] is False
        assert "غیرمجاز" in bad["message"]

        # Unsupported fields return Persian message
        unsup = await connector.update_database_user("sa", connection_limit=10)
        assert unsup["success"] is False
        assert "پشتیبانی نمی‌شود" in unsup["message"]


# ---------------------------------------------------------------------------
# 4. MySQL
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mysql_extract_and_update_users():
    connector = MySQLConnector({"host": "127.0.0.1", "port": 3306, "database": "mysql"})

    users_res = MagicMock()
    users_res.mappings.return_value.all.return_value = [
        {
            "User": "app_user",
            "Host": "10.0.0.%",
            "account_locked": "N",
            "Super_priv": "N",
            "Create_db_priv": "Y",
            "Create_user_priv": "N",
            "max_user_connections": 25,
            "password_lifetime": 90,
        },
        {
            "User": "root",
            "Host": "%",
            "account_locked": "N",
            "Super_priv": "Y",
            "Create_db_priv": "Y",
            "Create_user_priv": "Y",
            "max_user_connections": 0,
            "password_lifetime": None,
        },
    ]
    role_res = MagicMock()
    role_res.mappings.return_value.all.return_value = [
        {"FROM_USER": "app_role", "FROM_HOST": "%", "TO_USER": "app_user", "TO_HOST": "10.0.0.%"},
    ]
    proc_res = MagicMock()
    proc_res.mappings.return_value.all.return_value = [
        {"USER": "app_user", "active_sessions": 3},
    ]

    executed: list[str] = []
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(side_effect=[MagicMock(), MagicMock(), users_res, role_res, proc_res])

    mock_engine = MagicMock()
    mock_engine.connect.return_value.__aenter__.return_value = mock_conn
    mock_engine.connect.return_value.__aexit__.return_value = None
    mock_engine.dispose = AsyncMock()

    with patch.object(connector, "_create_engine", return_value=mock_engine):
        users = await connector.extract_database_users()
        assert len(users) == 2
        assert users[0]["username"] == "app_user@10.0.0.%"
        assert users[0]["can_login"] is True
        assert users[0]["can_create_db"] is True
        assert users[0]["connection_limit"] == 25
        assert users[0]["member_of_roles"] == ["app_role"]
        assert users[0]["active_sessions"] == 3
        assert users[1]["username"] == "root"  # '%' omitted
        assert users[1]["connection_limit"] == -1

        async def fake_exec(stmt):
            executed.append(str(stmt))
            return MagicMock()

        mock_conn.execute = fake_exec
        mock_conn.commit = AsyncMock()
        res = await connector.update_database_user("app_user@10.0.0.%", can_login=False, connection_limit=15)
        assert res["success"] is True
        assert any("ALTER USER 'app_user'@'10.0.0.%' ACCOUNT LOCK WITH MAX_USER_CONNECTIONS 15;" in s for s in executed)


# ---------------------------------------------------------------------------
# 5. Oracle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_oracle_extract_and_update_users():
    connector = OracleConnector({"host": "127.0.0.1", "port": 1521, "database_name": "ORCLCDB"})

    mock_cursor = AsyncMock()
    mock_cursor.fetchall = AsyncMock(side_effect=[
        [(101, "SYS", "OPEN", None), (102, "APP_USER", "LOCKED(TIMED)", datetime(2027, 5, 1))],
        [("SYS", "DBA"), ("APP_USER", "CONNECT")],
        [("SYS", 2)],
    ])
    mock_cursor.execute = AsyncMock()
    mock_cursor.close = AsyncMock()

    mock_conn = AsyncMock()
    mock_conn.cursor = MagicMock(return_value=mock_cursor)
    mock_conn.commit = AsyncMock()
    mock_conn.close = AsyncMock()

    with patch.object(connector, "_get_oracle_connection", return_value=mock_conn):
        users = await connector.extract_database_users()
        assert len(users) == 2
        assert users[0]["username"] == "SYS"
        assert users[0]["can_login"] is True
        assert users[0]["is_superuser"] is True
        assert users[0]["active_sessions"] == 2
        assert users[1]["username"] == "APP_USER"
        assert users[1]["can_login"] is False
        assert users[1]["valid_until"] == "2027-05-01T00:00:00"

        res = await connector.update_database_user("APP_USER", can_login=True)
        assert res["success"] is True
        mock_cursor.execute.assert_any_call('ALTER USER "APP_USER" ACCOUNT UNLOCK')


# ---------------------------------------------------------------------------
# 6. ClickHouse
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_clickhouse_extract_and_update_users():
    connector = ClickHouseConnector({"host": "127.0.0.1", "port": 8123})

    with patch.object(
        connector,
        "_query_json",
        side_effect=[
            [
                {"id": "u1", "name": "default", "auth_type": "sha256_password", "host_ip": ["0.0.0.0/0"]},
                {"id": "u2", "name": "readonly_user", "auth_type": "no_password", "host_ip": []},
            ],
            [{"user_name": "readonly_user", "granted_role_name": "reader"}],
            [{"user": "default", "active_sessions": 1}],
            [{"id": "r1", "name": "reader"}],
        ],
    ):
        users = await connector.extract_database_users()
        assert len(users) == 3
        assert users[0]["username"] == "default"
        assert users[0]["can_login"] is True
        assert users[0]["is_superuser"] is True
        assert users[1]["username"] == "readonly_user"
        assert users[1]["can_login"] is False
        assert users[1]["member_of_roles"] == ["reader"]
        assert users[2]["username"] == "reader"
        assert users[2]["can_login"] is False

    with patch.object(connector, "_execute_ddl", new_callable=AsyncMock) as mock_ddl:
        res = await connector.update_database_user("readonly_user", can_login=False, valid_until="2027-01-01")
        assert res["success"] is True
        mock_ddl.assert_called_once_with("ALTER USER `readonly_user` HOST NONE VALID UNTIL '2027-01-01'")

        unsup = await connector.update_database_user("readonly_user", connection_limit=5)
        assert unsup["success"] is False
        assert "SETTINGS PROFILE" in unsup["message"]


# ---------------------------------------------------------------------------
# 7. MongoDB
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mongodb_extract_and_update_users():
    connector = MongoDBConnector({"host": "127.0.0.1", "port": 27017, "database_name": "admin"})

    mock_admin_db = AsyncMock()
    mock_admin_db.command = AsyncMock(side_effect=[
        {
            "users": [
                {"_id": "admin.root", "user": "root", "db": "admin", "roles": [{"role": "root", "db": "admin"}]},
                {"_id": "admin.dev", "user": "dev", "db": "admin", "roles": [{"role": "userAdminAnyDatabase", "db": "admin"}]},
            ]
        },
        {
            "inprog": [
                {"effectiveUsers": [{"user": "root", "db": "admin"}]},
            ]
        },
    ])

    mock_client = MagicMock()
    mock_client.__getitem__.return_value = mock_admin_db
    mock_client.close = MagicMock()

    with patch.object(connector, "_get_client", return_value=mock_client):
        users = await connector.extract_database_users()
        assert len(users) == 2
        assert users[0]["username"] == "root"
        assert users[0]["is_superuser"] is True
        assert users[0]["active_sessions"] == 1
        assert users[1]["username"] == "dev"
        assert users[1]["can_create_role"] is True

        res = await connector.update_database_user("dev", can_login=False)
        assert res["success"] is False
        assert "MongoDB" in res["message"]
