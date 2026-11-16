import uuid
from datetime import datetime
import pytest
from sqlalchemy import inspect

from backend.src.models import (
    Base,
    CatalogColumn,
    CatalogTable,
    DatabaseConnection,
    SchemaChangeType,
    SyncSchedule,
)


def test_sync_job_model_attributes():
    """Verify SyncJob model attributes, constraints, and relationships."""
    from backend.src.models.sync_jobs import SyncJob

    mapper = inspect(SyncJob)
    assert mapper.persist_selectable.name == "sync_jobs"

    columns = {col.name: col for col in mapper.columns}
    expected_columns = {
        "id",
        "connection_id",
        "schedule_id",
        "status",
        "triggered_by",
        "started_at",
        "completed_at",
        "duration_ms",
        "tables_scanned",
        "tables_created",
        "tables_updated",
        "tables_deleted",
        "columns_scanned",
        "changes_detected",
        "error_message",
        "error_trace_jsonb",
        "created_at",
    }
    for col_name in expected_columns:
        assert col_name in columns, f"Column {col_name} missing from SyncJob"

    # Foreign keys
    fk_targets = {fk.target_fullname for fk in mapper.persist_selectable.foreign_keys}
    assert "database_connections.id" in fk_targets
    assert "sync_schedules.id" in fk_targets

    # Relationships
    relationships = {rel.key: rel for rel in mapper.relationships}
    assert "connection" in relationships
    assert "schedule" in relationships
    assert "statistics_history" in relationships


def test_table_statistics_history_model_attributes():
    """Verify TableStatisticsHistory model attributes, constraints, and relationships."""
    from backend.src.models.statistics import TableStatisticsHistory

    mapper = inspect(TableStatisticsHistory)
    assert mapper.persist_selectable.name == "table_statistics_history"

    columns = {col.name: col for col in mapper.columns}
    expected_columns = {
        "id",
        "table_id",
        "recorded_at",
        "row_count_estimate",
        "size_bytes",
        "sync_job_id",
    }
    for col_name in expected_columns:
        assert col_name in columns, f"Column {col_name} missing from TableStatisticsHistory"

    # Foreign keys
    fk_targets = {fk.target_fullname for fk in mapper.persist_selectable.foreign_keys}
    assert "catalog_tables.id" in fk_targets
    assert "sync_jobs.id" in fk_targets

    # Relationships
    relationships = {rel.key: rel for rel in mapper.relationships}
    assert "table" in relationships
    assert "sync_job" in relationships


def test_sync_schedule_circuit_breaker_extensions():
    """Verify SyncSchedule has circuit-breaker attributes."""
    mapper = inspect(SyncSchedule)
    columns = {col.name: col for col in mapper.columns}

    assert "consecutive_failures" in columns
    assert "circuit_state" in columns
    assert "cooldown_until" in columns

    # Verify relationships
    relationships = {rel.key: rel for rel in mapper.relationships}
    assert "sync_jobs" in relationships


def test_catalog_table_and_column_drift_extensions():
    """Verify CatalogTable and CatalogColumn have description_drift_detected."""
    table_mapper = inspect(CatalogTable)
    table_columns = {col.name: col for col in table_mapper.columns}
    assert "description_drift_detected" in table_columns

    table_rels = {rel.key: rel for rel in table_mapper.relationships}
    assert "statistics_history" in table_rels

    col_mapper = inspect(CatalogColumn)
    col_columns = {col.name: col for col in col_mapper.columns}
    assert "description_drift_detected" in col_columns


def test_database_connection_sync_jobs_relationship():
    """Verify DatabaseConnection has sync_jobs relationship."""
    mapper = inspect(DatabaseConnection)
    relationships = {rel.key: rel for rel in mapper.relationships}
    assert "sync_jobs" in relationships


def test_schema_change_type_description_drift():
    """Verify SchemaChangeType includes DESCRIPTION_DRIFT."""
    assert hasattr(SchemaChangeType, "DESCRIPTION_DRIFT")
    assert SchemaChangeType.DESCRIPTION_DRIFT.value == "DESCRIPTION_DRIFT"


def test_models_init_exports():
    """Verify models package exports SyncJob and TableStatisticsHistory."""
    import backend.src.models as models_pkg

    assert hasattr(models_pkg, "SyncJob"), "SyncJob must be exported in backend.src.models"
    assert hasattr(models_pkg, "TableStatisticsHistory"), "TableStatisticsHistory must be exported in backend.src.models"
