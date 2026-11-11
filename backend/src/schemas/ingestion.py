from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

from backend.src.models.catalog import ClassificationLevel, TableType


class IngestColumnPayload(BaseModel):
    name: str = Field(..., max_length=255)
    ordinal_position: int = Field(..., ge=1)
    data_type: str = Field(..., max_length=100)
    raw_data_type: str = Field(..., max_length=255)
    is_nullable: bool = True
    is_primary_key: bool = False
    is_foreign_key: bool = False
    default_value: Optional[str] = None
    source_description: Optional[str] = None
    classification_level: ClassificationLevel = ClassificationLevel.INTERNAL


class IngestTablePayload(BaseModel):
    name: str = Field(..., max_length=255)
    table_type: TableType = TableType.TABLE
    row_count_estimate: int = 0
    size_bytes: int = 0
    source_description: Optional[str] = None
    owner: Optional[str] = None
    custom_properties: Dict[str, Any] = Field(default_factory=dict)
    columns: List[IngestColumnPayload] = Field(default_factory=list)


class BulkIngestionRequest(BaseModel):
    connection_id: uuid.UUID
    schema_name: str = Field(..., max_length=255)
    schema_description: Optional[str] = None
    tables: List[IngestTablePayload] = Field(default_factory=list)


class BulkIngestionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    success: bool
    connection_id: uuid.UUID
    schema_id: uuid.UUID
    schema_name: str
    tables_upserted: int
    tables_deleted: int
    columns_upserted: int
    columns_deleted: int
    versions_created: int

