from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

from backend.src.schemas.pagination import KeysetPageInfo


class TagItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    color: str
    description: Optional[str] = None


class SearchHit(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    entity_type: str = Field(..., description="TABLE or COLUMN")
    name: str
    database_name: str
    schema_name: str
    table_name: Optional[str] = None
    table_type: Optional[str] = "TABLE"
    effective_description: Optional[str] = None
    rank: float
    classification_level: Optional[str] = None
    status: str
    row_count_estimate: Optional[int] = 0
    size_bytes: Optional[int] = 0
    tags: List[TagItem] = Field(default_factory=list)


class SearchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: List[SearchHit]
    page_info: KeysetPageInfo


class TableItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    schema_id: uuid.UUID
    name: str
    table_type: str
    row_count_estimate: int
    size_bytes: int
    effective_description: Optional[str] = None
    owner: Optional[str] = None
    status: str
    tags: List[TagItem] = Field(default_factory=list)


class TableListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: List[TableItem]
    page_info: KeysetPageInfo


class ColumnItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    table_id: uuid.UUID
    name: str
    ordinal_position: int
    data_type: str
    raw_data_type: str
    is_nullable: bool
    is_primary_key: bool
    is_foreign_key: bool
    default_value: Optional[str] = None
    source_description: Optional[str] = None
    approved_description: Optional[str] = None
    effective_description: Optional[str] = None
    classification_level: str
    status: str
    tags: List[TagItem] = Field(default_factory=list)


class TableDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    schema_id: uuid.UUID
    schema_name: str
    database_name: str
    name: str
    table_type: str
    row_count_estimate: int
    size_bytes: int
    source_description: Optional[str] = None
    approved_description: Optional[str] = None
    effective_description: Optional[str] = None
    owner: Optional[str] = None
    status: str
    columns: List[ColumnItem] = Field(default_factory=list)
    tags: List[TagItem] = Field(default_factory=list)
    categories: List[str] = Field(default_factory=list)
    custom_properties: Dict[str, Any] = Field(default_factory=dict)


class DescriptionUpdateRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    approved_description: str


class ClassificationUpdateRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    classification_level: str = Field(..., pattern="^(PUBLIC|INTERNAL|CONFIDENTIAL|PII)$")


class BulkTagTarget(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    entity_type: str = Field(..., pattern="^(DATABASE|SCHEMA|TABLE|COLUMN)$")
    entity_id: uuid.UUID


class BulkTagRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tag_ids: List[uuid.UUID]
    action: str = Field(..., pattern="^(ASSIGN|REMOVE)$")
    targets: List[BulkTagTarget]


class BulkTagResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    success: bool = True
    affected_count: int
