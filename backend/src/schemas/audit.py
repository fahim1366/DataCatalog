from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.src.schemas.pagination import KeysetPageInfo


class AuditLogItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    actor_id: Optional[uuid.UUID] = None
    actor_username: str
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    action: str
    entity_type: str
    entity_id: uuid.UUID
    diff_jsonb: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    @field_validator("ip_address", mode="before")
    @classmethod
    def convert_ip_address(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        return str(v)

    @field_validator("entity_type", mode="before")
    @classmethod
    def convert_entity_type(cls, v: Any) -> str:
        if hasattr(v, "value"):
            return str(v.value)
        return str(v)


class AuditLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: List[AuditLogItem]
    page_info: KeysetPageInfo

