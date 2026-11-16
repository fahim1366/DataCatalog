from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class TableStatisticPoint(BaseModel):
    recorded_at: datetime
    row_count_estimate: int
    size_bytes: int
    sync_job_id: Optional[uuid.UUID] = None

    model_config = ConfigDict(from_attributes=True)


class TableStatisticsHistoryResponse(BaseModel):
    table_id: uuid.UUID
    data_points: List[TableStatisticPoint]

    model_config = ConfigDict(from_attributes=True)
