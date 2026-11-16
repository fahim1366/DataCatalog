from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class DriftResolutionActionEnum(str, enum.Enum):
    RETAIN_APPROVED = "RETAIN_APPROVED"
    OVERWRITE_WITH_SOURCE = "OVERWRITE_WITH_SOURCE"
    CUSTOM_MERGE = "CUSTOM_MERGE"


class ColumnDriftDetail(BaseModel):
    column_id: uuid.UUID
    column_name: str
    source_description: Optional[str] = None
    approved_description: Optional[str] = None
    drift_detected: bool

    model_config = ConfigDict(from_attributes=True)


class DescriptionDriftDetailResponse(BaseModel):
    table_id: uuid.UUID
    table_name: str
    table_source_description: Optional[str] = None
    table_approved_description: Optional[str] = None
    drift_detected: bool
    column_drifts: List[ColumnDriftDetail] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class ColumnDriftResolutionRequest(BaseModel):
    column_id: uuid.UUID
    resolution_action: DriftResolutionActionEnum
    custom_description: Optional[str] = None


class ResolveDescriptionDriftRequest(BaseModel):
    resolution_action: DriftResolutionActionEnum
    custom_description: Optional[str] = None
    column_resolutions: Optional[List[ColumnDriftResolutionRequest]] = Field(default_factory=list)


class DescriptionDriftResolutionResponse(BaseModel):
    success: bool
    message: str
    resolved_at: datetime

    model_config = ConfigDict(from_attributes=True)
