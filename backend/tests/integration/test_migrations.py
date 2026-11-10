import uuid
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from backend.src.core.database import async_session_factory, engine


@pytest.mark.asyncio
async def test_baseline_tables_exist():
    """Verify that all Phase 1 catalog, connection, versioning, and audit tables exist."""
    required_tables = {
        "users",
        "roles",
        "user_roles",
        "permission_rules",
        "categories",
        "category_assignments",
        "database_connections",
        "sync_schedules",
        "schemas",
        "catalog_tables",
        "catalog_columns",
        "tags",
        "tag_assignments",
        "table_versions",
        "schema_change_logs",
        "audit_logs",
    }
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            )
        )
        existing_tables = {row[0] for row in result.fetchall()}
        missing = required_tables - existing_tables
        assert not missing, f"Missing required baseline tables: {missing}"


@pytest.mark.asyncio
async def test_baseline_enums_exist():
    """Verify that all PostgreSQL 16 enum types exist."""
    required_enums = {
        "user_status_enum",
        "permission_action_enum",
        "permission_effect_enum",
        "resource_type_enum",
        "entity_type_enum",
        "database_engine_enum",
        "table_type_enum",
        "lifecycle_status_enum",
        "classification_level_enum",
        "schema_change_type_enum",
    }
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT typname FROM pg_type WHERE typtype = 'e'")
        )
        existing_enums = {row[0] for row in result.fetchall()}
        missing = required_enums - existing_enums
        assert not missing, f"Missing required enums: {missing}"


@pytest.mark.asyncio
async def test_generated_columns_exist():
    """Verify STORED generated columns effective_description and search_vector exist."""
    async with engine.connect() as conn:
        # Check catalog_tables generated columns
        result = await conn.execute(
            text(
                "SELECT column_name, generation_expression FROM information_schema.columns "
                "WHERE table_name = 'catalog_tables' AND is_generated = 'ALWAYS'"
            )
        )
        table_gen_cols = {row[0] for row in result.fetchall()}
        assert "effective_description" in table_gen_cols
        assert "search_vector" in table_gen_cols

        # Check catalog_columns generated columns
        col_result = await conn.execute(
            text(
                "SELECT column_name, generation_expression FROM information_schema.columns "
                "WHERE table_name = 'catalog_columns' AND is_generated = 'ALWAYS'"
            )
        )
        col_gen_cols = {row[0] for row in col_result.fetchall()}
        assert "effective_description" in col_gen_cols
        assert "search_vector" in col_gen_cols


@pytest.mark.asyncio
async def test_high_performance_indexes_exist():
    """Verify exact composite B-Tree and GIN indexes required for sub-100ms scale."""
    required_indexes = {
        "idx_tables_keyset_schema",
        "idx_tables_keyset_global",
        "idx_columns_keyset_table",
        "idx_columns_classification",
        "idx_tables_search_vector_gin",
        "idx_columns_search_vector_gin",
        "idx_tables_name_trgm_gin",
        "idx_columns_name_trgm_gin",
        "idx_tag_assignments_lookup",
        "idx_perm_rules_eval",
        "idx_cat_assign_lookup",
    }
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
        )
        existing_indexes = {row[0] for row in result.fetchall()}
        missing = required_indexes - existing_indexes
        assert not missing, f"Missing high-performance indexes: {missing}"


@pytest.mark.asyncio
async def test_audit_logs_partitions_and_immutability():
    """Verify month partitions and immutability rules (UPDATE & DELETE prohibition)."""
    async with engine.connect() as conn:
        # 1. Verify partitions exist
        result = await conn.execute(
            text(
                "SELECT inhrelid::regclass::text FROM pg_inherits "
                "WHERE inhparent = 'audit_logs'::regclass"
            )
        )
        partitions = {row[0] for row in result.fetchall()}
        assert "audit_logs_y2026m11" in partitions
        assert "audit_logs_y2026m12" in partitions

        # 2. Verify immutability rules exist
        rule_result = await conn.execute(
            text(
                "SELECT rulename FROM pg_rules "
                "WHERE tablename = 'audit_logs'"
            )
        )
        rules = {row[0] for row in rule_result.fetchall()}
        assert "audit_logs_no_update" in rules
        assert "audit_logs_no_delete" in rules

    # 3. Functional test of immutability rule: UPDATE/DELETE must be rejected / no-op
    test_id = uuid.uuid4()
    async with async_session_factory() as session:
        # Insert audit log entry in current partition
        await session.execute(
            text(
                "INSERT INTO audit_logs (id, actor_username, action, entity_type, entity_id, diff_jsonb, created_at) "
                "VALUES (:id, 'admin', 'CREATE', 'DATABASE', :ent_id, '{\"key\": \"val\"}'::jsonb, '2026-11-10 12:00:00+00')"
            ),
            {"id": test_id, "ent_id": uuid.uuid4()},
        )
        await session.commit()

        # Attempt DELETE (should be blocked by audit_logs_no_delete rule)
        await session.execute(
            text("DELETE FROM audit_logs WHERE id = :id"),
            {"id": test_id},
        )
        await session.commit()

        # Verify record still exists
        verify = await session.execute(
            text("SELECT count(*) FROM audit_logs WHERE id = :id"),
            {"id": test_id},
        )
        assert verify.scalar_one() == 1, "Audit record was deleted despite immutability rule!"


@pytest.mark.asyncio
async def test_materialized_views_exist():
    """Verify concurrent materialized views for executive dashboards exist."""
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT matviewname FROM pg_matviews WHERE schemaname = 'public'")
        )
        matviews = {row[0] for row in result.fetchall()}
        assert "mv_metadata_completeness" in matviews
        assert "mv_classification_distribution" in matviews

