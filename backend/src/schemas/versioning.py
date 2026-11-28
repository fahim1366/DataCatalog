from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class TableVersionItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    table_id: uuid.UUID
    version_num: int
    created_by_username: Optional[str] = None
    created_at: datetime


class ModifiedColumnItem(BaseModel):
    column_name: str
    v1_attributes: Dict[str, Any]
    v2_attributes: Dict[str, Any]


class VersionDiffResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    table_id: uuid.UUID
    v1_num: int
    v2_num: int
    added_columns: List[Dict[str, Any]] = Field(default_factory=list)
    removed_columns: List[Dict[str, Any]] = Field(default_factory=list)
    modified_columns: List[ModifiedColumnItem] = Field(default_factory=list)


class DatabaseSnapshotPoint(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    timestamp: datetime
    label: str
    source: str  # "SYNC_JOB" | "SNAPSHOT"
    sync_job_id: Optional[uuid.UUID] = None
    tables_count: int = 0
    changes_count: int = 0


class DatabaseTableDiffItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    table_id: uuid.UUID
    schema_name: str
    table_name: str
    change_status: str  # "ADDED" | "DROPPED" | "MODIFIED"
    v1_version_num: Optional[int] = None
    v2_version_num: Optional[int] = None
    added_columns: List[Dict[str, Any]] = Field(default_factory=list)
    removed_columns: List[Dict[str, Any]] = Field(default_factory=list)
    modified_columns: List[ModifiedColumnItem] = Field(default_factory=list)


class DatabaseVersionDiffResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    connection_id: uuid.UUID
    v1_ts: datetime
    v2_ts: datetime
    added_tables_count: int = 0
    dropped_tables_count: int = 0
    modified_tables_count: int = 0
    unchanged_tables_count: int = 0
    total_column_changes: int = 0
    tables: List[DatabaseTableDiffItem] = Field(default_factory=list)

