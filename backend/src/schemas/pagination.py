from __future__ import annotations

from typing import Generic, List, Optional, TypeVar
from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class KeysetPageInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    has_next_page: bool = Field(..., description="True if subsequent pages exist")
    next_cursor: Optional[str] = Field(
        None, description="Base64-encoded keyset cursor for fetching next page"
    )
    limit: int = Field(..., description="Maximum number of items requested")


class KeysetPaginatedResponse(BaseModel, Generic[T]):
    model_config = ConfigDict(from_attributes=True)

    items: List[T] = Field(..., description="Array of entities on current page")
    page_info: KeysetPageInfo = Field(..., description="Keyset pagination metadata")

