from __future__ import annotations

import uuid
from datetime import datetime, timezone
import pytest
from pydantic import ValidationError

from backend.src.schemas.sync_jobs import (
    CircuitBreakerStatusResponse,
    ConnectionDiagnosticTestResponse,
    SyncJobDetailResponse,
    SyncJobListResponse,
    SyncJobResponse,
)
from backend.src.schemas.drift import (
    ColumnDriftDetail,
    ColumnDriftResolutionRequest,
    DescriptionDriftDetailResponse,
    DescriptionDriftResolutionResponse,
    ResolveDescriptionDriftRequest,
)
from backend.src.schemas.statistics import (
    TableStatisticPoint,
    TableStatisticsHistoryResponse,
)


def test_connection_diagnostic_test_response_schema():
    now = datetime.now(timezone.utc)
    data = {
        "success": True,
        "latency_ms": 12.5,
        "dialect": "POSTGRESQL",
        "server_version": "PostgreSQL 16.2",
        "message": "اتصال با موفقیت برقرار شد.",
        "diagnostic_details": {
            "tcp_reachable": True,
            "auth_valid": True,
            "read_only_supported": True,
            "statement_timeout_ms": 5000,
            "ssl_active": False,
        },
        "tested_at": now.isoformat(),
    }
    resp = ConnectionDiagnosticTestResponse.model_validate(data)
    assert resp.success is True
    assert resp.latency_ms == 12.5
    assert resp.dialect == "POSTGRESQL"
    assert resp.server_version == "PostgreSQL 16.2"
    assert resp.diagnostic_details["tcp_reachable"] is True


def test_sync_job_response_schema():
    now = datetime.now(timezone.utc)
    job_id = uuid.uuid4()
    conn_id = uuid.uuid4()
    data = {
        "id": job_id,
        "connection_id": conn_id,
        "schedule_id": None,
        "status": "QUEUED",
        "triggered_by": "MANUAL",
        "started_at": None,
        "completed_at": None,
        "duration_ms": None,
        "tables_scanned": 0,
        "tables_created": 0,
        "tables_updated": 0,
        "tables_deleted": 0,
        "columns_scanned": 0,
        "changes_detected": 0,
        "error_message": None,
        "created_at": now,
    }
    resp = SyncJobResponse.model_validate(data)
    assert resp.id == job_id
    assert resp.connection_id == conn_id
    assert resp.status == "QUEUED"
    assert resp.triggered_by == "MANUAL"


def test_sync_job_detail_response_schema():
    now = datetime.now(timezone.utc)
    job_id = uuid.uuid4()
    conn_id = uuid.uuid4()
    data = {
        "id": job_id,
        "connection_id": conn_id,
        "status": "PARTIAL_FAILURE",
        "triggered_by": "SCHEDULE",
        "created_at": now,
        "connection_name": "Analytics DB",
        "error_trace_jsonb": {
            "failed_schemas": [
                {
                    "schema_name": "restricted_schema",
                    "error_code": "42501",
                    "error_message": "permission denied for schema restricted_schema",
                }
            ],
            "exception_stack": None,
        },
    }
    detail = SyncJobDetailResponse.model_validate(data)
    assert detail.id == job_id
    assert detail.connection_name == "Analytics DB"
    assert len(detail.error_trace_jsonb["failed_schemas"]) == 1
    assert detail.error_trace_jsonb["failed_schemas"][0]["error_code"] == "42501"


def test_sync_job_list_response_schema():
    now = datetime.now(timezone.utc)
    job_id = uuid.uuid4()
    conn_id = uuid.uuid4()
    job_data = {
        "id": job_id,
        "connection_id": conn_id,
        "status": "SUCCESS",
        "triggered_by": "MANUAL",
        "created_at": now,
    }
    list_data = {
        "items": [job_data],
        "total": 1,
        "next_cursor": "cursor_token_xyz",
    }
    resp = SyncJobListResponse.model_validate(list_data)
    assert resp.total == 1
    assert len(resp.items) == 1
    assert resp.next_cursor == "cursor_token_xyz"


def test_circuit_breaker_status_response_schema():
    conn_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    data = {
        "connection_id": conn_id,
        "circuit_state": "OPEN",
        "consecutive_failures": 3,
        "cooldown_until": now,
    }
    resp = CircuitBreakerStatusResponse.model_validate(data)
    assert resp.connection_id == conn_id
    assert resp.circuit_state == "OPEN"
    assert resp.consecutive_failures == 3
    assert resp.cooldown_until == now


def test_description_drift_detail_response_schema():
    table_id = uuid.uuid4()
    col_id = uuid.uuid4()
    data = {
        "table_id": table_id,
        "table_name": "orders",
        "table_source_description": "Legacy orders comment",
        "table_approved_description": "Curated orders table description",
        "drift_detected": True,
        "column_drifts": [
            {
                "column_id": col_id,
                "column_name": "total_amount",
                "source_description": "DB amount comment",
                "approved_description": "Curated amount description",
                "drift_detected": True,
            }
        ],
    }
    resp = DescriptionDriftDetailResponse.model_validate(data)
    assert resp.table_id == table_id
    assert resp.drift_detected is True
    assert len(resp.column_drifts) == 1
    assert resp.column_drifts[0].column_id == col_id


def test_resolve_description_drift_request_and_response_schema():
    col_id = uuid.uuid4()
    req_data = {
        "resolution_action": "CUSTOM_MERGE",
        "custom_description": "Merged description text",
        "column_resolutions": [
            {
                "column_id": col_id,
                "resolution_action": "RETAIN_APPROVED",
                "custom_description": None,
            }
        ],
    }
    req = ResolveDescriptionDriftRequest.model_validate(req_data)
    assert req.resolution_action == "CUSTOM_MERGE"
    assert req.custom_description == "Merged description text"
    assert len(req.column_resolutions) == 1

    # Invalid resolution action raises ValidationError
    with pytest.raises(ValidationError):
        ResolveDescriptionDriftRequest.model_validate({"resolution_action": "INVALID_ACTION"})

    now = datetime.now(timezone.utc)
    resp_data = {
        "success": True,
        "message": "Drift resolved successfully.",
        "resolved_at": now,
    }
    resp = DescriptionDriftResolutionResponse.model_validate(resp_data)
    assert resp.success is True
    assert resp.resolved_at == now


def test_table_statistics_history_response_schema():
    table_id = uuid.uuid4()
    sync_job_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    data = {
        "table_id": table_id,
        "data_points": [
            {
                "recorded_at": now,
                "row_count_estimate": 1500000,
                "size_bytes": 104857600,
                "sync_job_id": sync_job_id,
            }
        ],
    }
    resp = TableStatisticsHistoryResponse.model_validate(data)
    assert resp.table_id == table_id
    assert len(resp.data_points) == 1
    assert resp.data_points[0].row_count_estimate == 1500000
    assert resp.data_points[0].size_bytes == 104857600
    assert resp.data_points[0].sync_job_id == sync_job_id
