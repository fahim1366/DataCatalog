from __future__ import annotations

import uuid
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class DatabaseCompletenessItem(BaseModel):
    """Completeness metrics breakdown for an individual connected database."""
    model_config = ConfigDict(from_attributes=True)

    connection_id: uuid.UUID
    database_name: str
    table_count: int
    tables_with_desc: int
    tables_with_owner: int
    completeness_pct: float


class CompletenessReport(BaseModel):
    """Aggregated executive metadata completeness report across all databases."""
    model_config = ConfigDict(from_attributes=True)

    overall_completeness_pct: float
    total_databases: int
    total_tables: int
    total_columns: int
    database_breakdown: List[DatabaseCompletenessItem]


class ClassificationBreakdownItem(BaseModel):
    """Breakdown of classified columns and affected tables for a security classification level."""
    model_config = ConfigDict(from_attributes=True)

    classification_level: str
    column_count: int
    affected_table_count: int


class ClassificationReport(BaseModel):
    """Aggregated classification and PII distribution report across all connected databases."""
    model_config = ConfigDict(from_attributes=True)

    total_classified_columns: int
    breakdown: List[ClassificationBreakdownItem]


class ReportRefreshResponse(BaseModel):
    """Response payload returned when materialized views are refreshed concurrently."""
    model_config = ConfigDict(from_attributes=True)

    status: str = "SUCCESS"
    message: str = "Materialized views refreshed concurrently."
    refreshed_views: List[str] = Field(
        default_factory=lambda: [
            "mv_metadata_completeness",
            "mv_classification_distribution",
        ]
    )

