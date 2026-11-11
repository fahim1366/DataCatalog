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

