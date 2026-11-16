from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class SyncJobStatusEnum(str, enum.Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    PARTIAL_FAILURE = "PARTIAL_FAILURE"
    FAILED = "FAILED"


class SyncTriggerTypeEnum(str, enum.Enum):
    SCHEDULE = "SCHEDULE"
    MANUAL = "MANUAL"
    RETRY = "RETRY"


class CircuitStateEnum(str, enum.Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class ConnectionDiagnosticTestResponse(BaseModel):
    success: bool
    latency_ms: float
    dialect: str
    server_version: Optional[str] = None
    message: str
    diagnostic_details: Optional[Dict[str, Any]] = None
    tested_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SyncJobResponse(BaseModel):
    id: uuid.UUID
    connection_id: uuid.UUID
    schedule_id: Optional[uuid.UUID] = None
    status: str
    triggered_by: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    tables_scanned: int = 0
    tables_created: int = 0
    tables_updated: int = 0
    tables_deleted: int = 0
    columns_scanned: int = 0
    changes_detected: int = 0
    error_message: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SyncJobDetailResponse(SyncJobResponse):
    connection_name: Optional[str] = None
    error_trace_jsonb: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)


class SyncJobListResponse(BaseModel):
    items: List[SyncJobResponse]
    total: int
    next_cursor: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class CircuitBreakerStatusResponse(BaseModel):
    connection_id: uuid.UUID
    circuit_state: str
    consecutive_failures: int
    cooldown_until: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
