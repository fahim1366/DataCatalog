from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.src.models.connections import DatabaseEngine
from backend.src.core.cron_validator import validate_cron_expression


class SyncScheduleResponse(BaseModel):
    id: uuid.UUID
    connection_id: uuid.UUID
    cron_expression: str
    is_enabled: bool
    consecutive_failures: int = 0
    circuit_state: str = "CLOSED"
    cooldown_until: Optional[datetime] = None
    last_run_at: Optional[datetime] = None
    last_run_status: Optional[str] = None
    last_run_message: Optional[str] = None
    next_run_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SyncScheduleUpdateRequest(BaseModel):
    cron_expression: str = Field(..., description="5-part cron schedule expression")
    is_enabled: bool = Field(default=True, description="Whether the schedule is active")

    @field_validator("cron_expression")
    @classmethod
    def validate_cron(cls, v: str) -> str:
        validate_cron_expression(v)
        return v


class ConnectionCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200, description="Unique connection profile name")
    engine_type: DatabaseEngine = Field(..., description="Target database engine dialect")
    host: str = Field(..., min_length=1, max_length=255, description="Host address or hostname")
    port: int = Field(..., ge=1, le=65535, description="Network port")
    database_name: str = Field(..., min_length=1, max_length=255, description="Target database name or catalog")
    default_schema: Optional[str] = Field(None, max_length=100, description="Default schema to inspect")
    owner: Optional[str] = Field(None, max_length=255, description="Data steward or administrator owner")
    category_id: Optional[uuid.UUID] = Field(None, description="Assigned category / topic leaf ID")
    credentials: Dict[str, Any] = Field(..., description="Sensitive connection credentials to encrypt with DEK")
    custom_params: Dict[str, Any] = Field(default_factory=dict, description="Dialect-specific driver parameters")
    cron_expression: Optional[str] = Field(None, description="Optional 5-part cron schedule for background sync")

    @field_validator("cron_expression")
    @classmethod
    def validate_cron(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            validate_cron_expression(v)
        return v


class ConnectionUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    engine_type: Optional[DatabaseEngine] = None
    host: Optional[str] = Field(None, min_length=1, max_length=255)
    port: Optional[int] = Field(None, ge=1, le=65535)
    database_name: Optional[str] = Field(None, min_length=1, max_length=255)
    default_schema: Optional[str] = Field(None, max_length=100)
    owner: Optional[str] = Field(None, max_length=255)
    category_id: Optional[uuid.UUID] = None
    credentials: Optional[Dict[str, Any]] = None
    custom_params: Optional[Dict[str, Any]] = None
    cron_expression: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator("cron_expression")
    @classmethod
    def validate_cron(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v.strip():
            validate_cron_expression(v.strip())
        return v


class ConnectionResponse(BaseModel):
    id: uuid.UUID
    name: str
    engine_type: DatabaseEngine
    host: str
    port: int
    database_name: str
    default_schema: Optional[str] = None
    collation: Optional[str] = None
    owner: str
    is_active: bool
    category_id: Optional[uuid.UUID] = None
    category_name: Optional[str] = None
    custom_params: Dict[str, Any] = Field(default_factory=dict)
    masked_credentials: Dict[str, str] = Field(default_factory=dict)
    sync_schedule: Optional[SyncScheduleResponse] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ConnectionTestResponse(BaseModel):
    success: bool
    latency_ms: float
    dialect: DatabaseEngine
    message: str
    diagnostic_details: Optional[Dict[str, Any]] = None
    tested_at: datetime


class DiscoverDatabasesRequest(BaseModel):
    engine_type: DatabaseEngine
    host: str = Field(..., min_length=1, max_length=255)
    port: int = Field(..., ge=1, le=65535)
    credentials: Dict[str, Any] = Field(..., description="Credentials containing username and password")
    custom_params: Dict[str, Any] = Field(default_factory=dict)


class DiscoverDatabasesResponse(BaseModel):
    success: bool
    databases: List[str] = Field(default_factory=list)
    latency_ms: float
    server_version: Optional[str] = None
    message: str


class TestUnsavedConnectionRequest(BaseModel):
    engine_type: DatabaseEngine
    host: str = Field(..., min_length=1, max_length=255)
    port: int = Field(..., ge=1, le=65535)
    database_name: str = Field(..., min_length=1, max_length=255)
    credentials: Dict[str, Any] = Field(..., description="Credentials containing username and password")
    custom_params: Dict[str, Any] = Field(default_factory=dict)
