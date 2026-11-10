"""Baseline Enterprise Catalog Schema, Partitions & Materialized Views

Revision ID: 002_baseline_schema
Revises: 001_users_and_rbac
Create Date: 2026-11-10 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "002_baseline_schema"
down_revision: Union[str, None] = "001_users_and_rbac"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Idempotent ENUM Types
    op.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'database_engine_enum') THEN
            CREATE TYPE database_engine_enum AS ENUM ('POSTGRESQL', 'MSSQL', 'ORACLE', 'MYSQL', 'CLICKHOUSE');
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'table_type_enum') THEN
            CREATE TYPE table_type_enum AS ENUM ('TABLE', 'VIEW', 'MATERIALIZED_VIEW', 'EXTERNAL_TABLE');
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'lifecycle_status_enum') THEN
            CREATE TYPE lifecycle_status_enum AS ENUM ('ACTIVE', 'DEPRECATED', 'DELETED');
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'classification_level_enum') THEN
            CREATE TYPE classification_level_enum AS ENUM ('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'PII');
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'schema_change_type_enum') THEN
            CREATE TYPE schema_change_type_enum AS ENUM (
                'COLUMN_ADDED', 'COLUMN_DROPPED', 'TYPE_CHANGED',
                'NULLABILITY_CHANGED', 'DESCRIPTION_UPDATED', 'TAG_ASSIGNED', 'TAG_REMOVED'
            );
        END IF;
    END$$;
    """)

    # 2. Database Connections & Sync Schedules
    op.execute("""
    CREATE TABLE IF NOT EXISTS database_connections (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        name VARCHAR(200) NOT NULL UNIQUE,
        engine_type database_engine_enum NOT NULL,
        host VARCHAR(255) NOT NULL,
        port INTEGER NOT NULL,
        database_name VARCHAR(255) NOT NULL,
        default_schema VARCHAR(100),
        owner VARCHAR(255) NOT NULL,
        kek_version INTEGER NOT NULL DEFAULT 1,
        encrypted_dek BYTEA NOT NULL,
        encrypted_credentials BYTEA NOT NULL,
        custom_params JSONB NOT NULL DEFAULT '{}'::jsonb,
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
    );

    CREATE TABLE IF NOT EXISTS sync_schedules (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        connection_id UUID NOT NULL UNIQUE REFERENCES database_connections(id) ON DELETE CASCADE,
        cron_expression VARCHAR(100) NOT NULL,
        is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
        last_run_at TIMESTAMPTZ,
        last_run_status VARCHAR(50),
        last_run_message TEXT,
        next_run_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
    );
    """)

    # 3. Core Metadata Hierarchy (Schemas, CatalogTables, CatalogColumns)
    op.execute("""
    CREATE TABLE IF NOT EXISTS schemas (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        connection_id UUID NOT NULL REFERENCES database_connections(id) ON DELETE CASCADE,
        name VARCHAR(255) NOT NULL,
        description TEXT,
        owner VARCHAR(255),
        status lifecycle_status_enum NOT NULL DEFAULT 'ACTIVE',
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT uq_connection_schema UNIQUE (connection_id, name)
    );

    CREATE TABLE IF NOT EXISTS catalog_tables (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        schema_id UUID NOT NULL REFERENCES schemas(id) ON DELETE CASCADE,
        name VARCHAR(255) NOT NULL,
        table_type table_type_enum NOT NULL DEFAULT 'TABLE',
        row_count_estimate BIGINT NOT NULL DEFAULT 0,
        size_bytes BIGINT NOT NULL DEFAULT 0,
        source_description TEXT,
        approved_description TEXT,
        effective_description TEXT GENERATED ALWAYS AS (
            COALESCE(NULLIF(source_description, ''), approved_description)
        ) STORED,
        owner VARCHAR(255),
        status lifecycle_status_enum NOT NULL DEFAULT 'ACTIVE',
        custom_properties JSONB NOT NULL DEFAULT '{}'::jsonb,
        search_vector tsvector GENERATED ALWAYS AS (
            to_tsvector('english',
                coalesce(name, '') || ' ' ||
                coalesce(source_description, '') || ' ' ||
                coalesce(approved_description, '') || ' ' ||
                coalesce(owner, '')
            )
        ) STORED,
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT uq_schema_table UNIQUE (schema_id, name)
    );

    CREATE TABLE IF NOT EXISTS catalog_columns (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        table_id UUID NOT NULL REFERENCES catalog_tables(id) ON DELETE CASCADE,
        name VARCHAR(255) NOT NULL,
        ordinal_position INTEGER NOT NULL,
        data_type VARCHAR(100) NOT NULL,
        raw_data_type VARCHAR(255) NOT NULL,
        is_nullable BOOLEAN NOT NULL DEFAULT TRUE,
        is_primary_key BOOLEAN NOT NULL DEFAULT FALSE,
        is_foreign_key BOOLEAN NOT NULL DEFAULT FALSE,
        default_value TEXT,
        source_description TEXT,
        approved_description TEXT,
        effective_description TEXT GENERATED ALWAYS AS (
            COALESCE(NULLIF(source_description, ''), approved_description)
        ) STORED,
        classification_level classification_level_enum NOT NULL DEFAULT 'INTERNAL',
        status lifecycle_status_enum NOT NULL DEFAULT 'ACTIVE',
        search_vector tsvector GENERATED ALWAYS AS (
            to_tsvector('english',
                coalesce(name, '') || ' ' ||
                coalesce(data_type, '') || ' ' ||
                coalesce(source_description, '') || ' ' ||
                coalesce(approved_description, '')
            )
        ) STORED,
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT uq_table_column UNIQUE (table_id, name)
    );
    """)

    # 4. Tags & Assignments
    op.execute("""
    CREATE TABLE IF NOT EXISTS tags (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        name VARCHAR(100) NOT NULL UNIQUE,
        color VARCHAR(7) NOT NULL DEFAULT '#1976D2',
        description TEXT,
        is_governed BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
    );

    CREATE TABLE IF NOT EXISTS tag_assignments (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tag_id UUID NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
        entity_type entity_type_enum NOT NULL,
        entity_id UUID NOT NULL,
        assigned_by UUID REFERENCES users(id) ON DELETE SET NULL,
        assigned_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT uq_tag_assignment UNIQUE (tag_id, entity_type, entity_id)
    );
    """)

    # 5. Table History & Schema Versioning
    op.execute("""
    CREATE TABLE IF NOT EXISTS table_versions (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        table_id UUID NOT NULL REFERENCES catalog_tables(id) ON DELETE CASCADE,
        version_num INTEGER NOT NULL,
        snapshot_jsonb JSONB NOT NULL,
        created_by UUID REFERENCES users(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT uq_table_version UNIQUE (table_id, version_num)
    );

    CREATE TABLE IF NOT EXISTS schema_change_logs (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        table_id UUID NOT NULL REFERENCES catalog_tables(id) ON DELETE CASCADE,
        version_id UUID REFERENCES table_versions(id) ON DELETE SET NULL,
        change_type schema_change_type_enum NOT NULL,
        column_name VARCHAR(255),
        old_value JSONB,
        new_value JSONB,
        actor_id UUID REFERENCES users(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
    );
    """)

    # 6. Partitioned Immutable Audit Logs & Immutability Rules
    op.execute("""
    CREATE TABLE IF NOT EXISTS audit_logs (
        id UUID NOT NULL DEFAULT gen_random_uuid(),
        actor_id UUID REFERENCES users(id) ON DELETE SET NULL,
        actor_username VARCHAR(100) NOT NULL,
        ip_address INET,
        user_agent VARCHAR(500),
        action VARCHAR(100) NOT NULL,
        entity_type entity_type_enum NOT NULL,
        entity_id UUID NOT NULL,
        diff_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY (id, created_at)
    ) PARTITION BY RANGE (created_at);

    CREATE TABLE IF NOT EXISTS audit_logs_y2026m11 PARTITION OF audit_logs
        FOR VALUES FROM ('2026-11-01 00:00:00+00') TO ('2026-12-01 00:00:00+00');
    CREATE TABLE IF NOT EXISTS audit_logs_y2026m12 PARTITION OF audit_logs
        FOR VALUES FROM ('2026-12-01 00:00:00+00') TO ('2027-01-01 00:00:00+00');
    CREATE TABLE IF NOT EXISTS audit_logs_y2027m01 PARTITION OF audit_logs
        FOR VALUES FROM ('2027-01-01 00:00:00+00') TO ('2027-02-01 00:00:00+00');
    CREATE TABLE IF NOT EXISTS audit_logs_y2027m02 PARTITION OF audit_logs
        FOR VALUES FROM ('2027-02-01 00:00:00+00') TO ('2027-03-01 00:00:00+00');

    -- Prohibit UPDATE and DELETE on audit_logs
    DROP RULE IF EXISTS audit_logs_no_update ON audit_logs;
    CREATE RULE audit_logs_no_update AS ON UPDATE TO audit_logs DO INSTEAD NOTHING;

    DROP RULE IF EXISTS audit_logs_no_delete ON audit_logs;
    CREATE RULE audit_logs_no_delete AS ON DELETE TO audit_logs DO INSTEAD NOTHING;
    """)

    # 7. High-Performance Composite B-Tree & GIN Indexes (Sub-100ms across 160k tables & 5M columns)
    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_tables_keyset_schema ON catalog_tables (schema_id, name ASC, id ASC)
        WHERE status != 'DELETED';
    CREATE INDEX IF NOT EXISTS idx_tables_keyset_global ON catalog_tables (name ASC, id ASC)
        WHERE status != 'DELETED';
    CREATE INDEX IF NOT EXISTS idx_columns_keyset_table ON catalog_columns (table_id, ordinal_position ASC, id ASC)
        WHERE status != 'DELETED';
    CREATE INDEX IF NOT EXISTS idx_columns_classification ON catalog_columns (classification_level, table_id);

    CREATE INDEX IF NOT EXISTS idx_tables_search_vector_gin ON catalog_tables USING GIN (search_vector);
    CREATE INDEX IF NOT EXISTS idx_columns_search_vector_gin ON catalog_columns USING GIN (search_vector);

    CREATE INDEX IF NOT EXISTS idx_tables_name_trgm_gin ON catalog_tables USING GIN (name gin_trgm_ops);
    CREATE INDEX IF NOT EXISTS idx_columns_name_trgm_gin ON catalog_columns USING GIN (name gin_trgm_ops);

    CREATE INDEX IF NOT EXISTS idx_tag_assignments_lookup ON tag_assignments (entity_type, entity_id, tag_id);
    CREATE INDEX IF NOT EXISTS idx_audit_logs_actor_created ON audit_logs (actor_id, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_audit_logs_entity_created ON audit_logs (entity_type, entity_id, created_at DESC);
    """)

    # 8. PostgreSQL Materialized Views for Executive Governance Dashboard
    op.execute("""
    DROP MATERIALIZED VIEW IF EXISTS mv_metadata_completeness CASCADE;
    CREATE MATERIALIZED VIEW mv_metadata_completeness AS
    SELECT 
        d.id AS connection_id,
        d.name AS database_name,
        COUNT(DISTINCT s.id) AS schema_count,
        COUNT(DISTINCT t.id) AS table_count,
        COUNT(DISTINCT c.id) AS column_count,
        COUNT(DISTINCT t.id) FILTER (WHERE t.effective_description IS NOT NULL AND t.effective_description <> '') AS tables_with_desc,
        COUNT(DISTINCT t.id) FILTER (WHERE t.owner IS NOT NULL AND t.owner <> '') AS tables_with_owner,
        ROUND(
            (COUNT(DISTINCT t.id) FILTER (WHERE t.effective_description IS NOT NULL AND t.effective_description <> '')::numeric / 
             NULLIF(COUNT(DISTINCT t.id), 0)) * 100, 2
        ) AS table_desc_completeness_pct
    FROM database_connections d
    LEFT JOIN schemas s ON s.connection_id = d.id AND s.status != 'DELETED'
    LEFT JOIN catalog_tables t ON t.schema_id = s.id AND t.status != 'DELETED'
    LEFT JOIN catalog_columns c ON c.table_id = t.id AND c.status != 'DELETED'
    GROUP BY d.id, d.name;

    CREATE UNIQUE INDEX IF NOT EXISTS uq_mv_metadata_completeness ON mv_metadata_completeness (connection_id);

    DROP MATERIALIZED VIEW IF EXISTS mv_classification_distribution CASCADE;
    CREATE MATERIALIZED VIEW mv_classification_distribution AS
    SELECT 
        c.classification_level,
        COUNT(c.id) AS column_count,
        COUNT(DISTINCT c.table_id) AS affected_table_count
    FROM catalog_columns c
    WHERE c.status != 'DELETED'
    GROUP BY c.classification_level;

    CREATE UNIQUE INDEX IF NOT EXISTS uq_mv_classification_distribution ON mv_classification_distribution (classification_level);
    """)


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_classification_distribution CASCADE;")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_metadata_completeness CASCADE;")

    op.execute("DROP TABLE IF EXISTS audit_logs CASCADE;")
    op.execute("DROP TABLE IF EXISTS schema_change_logs CASCADE;")
    op.execute("DROP TABLE IF EXISTS table_versions CASCADE;")
    op.execute("DROP TABLE IF EXISTS tag_assignments CASCADE;")
    op.execute("DROP TABLE IF EXISTS tags CASCADE;")
    op.execute("DROP TABLE IF EXISTS catalog_columns CASCADE;")
    op.execute("DROP TABLE IF EXISTS catalog_tables CASCADE;")
    op.execute("DROP TABLE IF EXISTS schemas CASCADE;")
    op.execute("DROP TABLE IF EXISTS sync_schedules CASCADE;")
    op.execute("DROP TABLE IF EXISTS database_connections CASCADE;")

    op.execute("DROP TYPE IF EXISTS schema_change_type_enum;")
    op.execute("DROP TYPE IF EXISTS classification_level_enum;")
    op.execute("DROP TYPE IF EXISTS lifecycle_status_enum;")
    op.execute("DROP TYPE IF EXISTS table_type_enum;")
    op.execute("DROP TYPE IF EXISTS database_engine_enum;")

