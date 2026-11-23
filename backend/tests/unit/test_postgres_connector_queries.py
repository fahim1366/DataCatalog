import re
import pytest

from sqlalchemy.pool import NullPool

from backend.src.services.connectors.postgres import PostgresConnector


def test_postgres_connector_ephemeral_engine_configuration():
    """Verify PostgresConnector uses NullPool and connect_timeout=10 in connect_args."""
    config = {
        "host": "localhost",
        "port": 5432,
        "database": "test_db",
        "username": "postgres",
        "password": "secret_password",
    }
    connector = PostgresConnector(config)
    assert connector.get_pool_class() == NullPool
    
    connect_args = connector.get_connect_args()
    assert connect_args.get("connect_timeout") == 10 or connect_args.get("timeout") == 10
    
    # Verify session statements contain timeouts and read-only mode
    statements = connector.get_session_setup_statements()
    statements_str = " ".join(statements)
    assert "statement_timeout = 30000" in statements_str
    assert "idle_in_transaction_session_timeout = 10000" in statements_str
    assert "READ ONLY" in statements_str


def test_postgres_connector_queries_use_pg_catalog_and_zero_information_schema():
    """
    Verify all introspection SQL queries target pg_catalog system tables
    and do NOT query information_schema views (except to filter it out in schema listing).
    """
    queries = PostgresConnector.QUERIES

    # 1. Schemas query
    schemas_sql = queries["SCHEMAS"]
    assert "pg_catalog.pg_namespace" in schemas_sql or "pg_namespace" in schemas_sql
    assert "pg_catalog.pg_description" in schemas_sql or "pg_description" in schemas_sql
    assert "pg_temp" in schemas_sql
    assert "pg_toast" in schemas_sql
    # Ensure information_schema is only mentioned as a filter exclusion
    assert "FROM information_schema" not in schemas_sql
    assert "JOIN information_schema" not in schemas_sql

    # 2. Tables query
    tables_sql = queries["TABLES"]
    assert "pg_catalog.pg_class" in tables_sql or "pg_class" in tables_sql
    assert "pg_catalog.pg_namespace" in tables_sql or "pg_namespace" in tables_sql
    assert "pg_catalog.pg_description" in tables_sql or "pg_description" in tables_sql
    assert "pg_stat_user_tables" in tables_sql
    assert "information_schema" not in tables_sql
    # relkinds: r (table), v (view), m (materialized view), p (partitioned table), f (foreign/external)
    for relkind in ["'r'", "'v'", "'m'", "'p'", "'f'"]:
        assert relkind in tables_sql

    # 3. Columns query
    columns_sql = queries["COLUMNS"]
    assert "pg_catalog.pg_attribute" in columns_sql or "pg_attribute" in columns_sql
    assert "pg_catalog.pg_type" in columns_sql or "pg_type" in columns_sql
    assert "pg_catalog.pg_attrdef" in columns_sql or "pg_attrdef" in columns_sql
    assert "pg_catalog.pg_constraint" in columns_sql or "pg_constraint" in columns_sql
    assert "pg_collation" in columns_sql or "pg_catalog.pg_collation" in columns_sql
    assert "attcollation" in columns_sql
    assert "information_schema" not in columns_sql
    assert "attnum > 0" in columns_sql
    assert "attisdropped" in columns_sql

    # 4. Foreign Keys query
    fk_sql = queries["FOREIGN_KEYS"]
    assert "pg_catalog.pg_constraint" in fk_sql or "pg_constraint" in fk_sql
    assert "contype = 'f'" in fk_sql
    assert "information_schema" not in fk_sql

    # 5. Statistics query
    stats_sql = queries["STATISTICS"]
    assert "pg_catalog.pg_class" in stats_sql or "pg_class" in stats_sql
    assert "n_live_tup" in stats_sql or "reltuples" in stats_sql
    assert "pg_total_relation_size" in stats_sql
    assert "information_schema" not in stats_sql


def test_postgres_connector_url_building():
    """Verify safe asyncpg connection URL construction."""
    config = {
        "host": "db.internal",
        "port": 5433,
        "database": "analytics",
        "username": "analyst_user",
        "password": "p@ssword!#",
        "custom_params": {"sslmode": "require"},
    }
    connector = PostgresConnector(config)
    url = connector.build_connection_url()
    assert url.startswith("postgresql+asyncpg://")
    assert "analyst_user" in url
    assert "db.internal:5433" in url
    assert "analytics" in url
