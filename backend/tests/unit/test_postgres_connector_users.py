from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.src.services.connectors.postgres import PostgresConnector


@pytest.mark.asyncio
async def test_postgres_connector_extract_users_query_structure():
    """Verifies that DB_USERS query contains all required pg_catalog columns."""
    connector = PostgresConnector({"host": "127.0.0.1", "port": 5432, "database": "postgres"})
    query = connector.QUERIES.get("DB_USERS", "")

    assert "pg_catalog.pg_roles" in query
    assert "pg_catalog.pg_auth_members" in query
    assert "pg_catalog.pg_stat_activity" in query
    assert "rolcanlogin" in query
    assert "rolsuper" in query
    assert "rolcreatedb" in query
    assert "rolcreaterole" in query
    assert "rolconnlimit" in query
    assert "rolvaliduntil" in query


@pytest.mark.asyncio
async def test_postgres_connector_update_user_identifier_quoting():
    """Verifies that safe identifier quoting is used in ALTER ROLE statements."""
    connector = PostgresConnector({"host": "127.0.0.1", "port": 5432, "database": "postgres"})

    executed_sqls = []

    mock_conn = AsyncMock()
    async def mock_execute(stmt):
        executed_sqls.append(str(stmt))
        return MagicMock()

    mock_conn.execute = mock_execute
    mock_conn.commit = AsyncMock()

    mock_engine = MagicMock()
    mock_engine.connect.return_value.__aenter__.return_value = mock_conn
    mock_engine.connect.return_value.__aexit__.return_value = None
    mock_engine.dispose = AsyncMock()

    with patch.object(connector, "_create_engine", return_value=mock_engine):
        # 1. Standard username with quotes
        res = await connector.update_database_user(
            username='app_user',
            can_login=False,
            connection_limit=10,
            valid_until='2027-01-01',
        )
        assert res["success"] is True
        alter_sql = [s for s in executed_sqls if "ALTER ROLE" in s][0]
        assert 'ALTER ROLE "app_user"' in alter_sql
        assert "NOLOGIN" in alter_sql
        assert "CONNECTION LIMIT 10" in alter_sql
        assert "VALID UNTIL '2027-01-01'" in alter_sql

        # 2. Username with internal double-quote: must be escaped as double double-quotes
        executed_sqls.clear()
        res2 = await connector.update_database_user(
            username='weird"user',
            can_login=True,
            connection_limit=-1,
            valid_until='infinity',
        )
        assert res2["success"] is True
        alter_sql2 = [s for s in executed_sqls if "ALTER ROLE" in s][0]
        assert 'ALTER ROLE "weird""user"' in alter_sql2
        assert "LOGIN" in alter_sql2
        assert "CONNECTION LIMIT -1" in alter_sql2
        assert "VALID UNTIL 'infinity'" in alter_sql2


@pytest.mark.asyncio
async def test_postgres_connector_update_user_insufficient_privilege():
    """Verifies that InsufficientPrivilegeError / 42501 yields Persian explanation."""
    connector = PostgresConnector({"host": "127.0.0.1", "port": 5432, "database": "postgres"})

    mock_conn = AsyncMock()
    class MockPrivilegeError(Exception):
        pgcode = "42501"

    async def mock_execute(stmt):
        if "ALTER ROLE" in str(stmt):
            raise MockPrivilegeError("permission denied to alter role")
        return MagicMock()

    mock_conn.execute = mock_execute
    mock_engine = MagicMock()
    mock_engine.connect.return_value.__aenter__.return_value = mock_conn
    mock_engine.connect.return_value.__aexit__.return_value = None
    mock_engine.dispose = AsyncMock()

    with patch.object(connector, "_create_engine", return_value=mock_engine):
        res = await connector.update_database_user(
            username='admin_role',
            can_login=False,
        )
        assert res["success"] is False
        assert res["insufficient_privilege"] is True
        assert "CREATEROLE" in res["message"]
        assert "مجوز کافی" in res["message"]
