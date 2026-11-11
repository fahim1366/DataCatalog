from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.models.audit import AuditLog
from backend.src.models.rbac import EntityType
from backend.src.schemas.audit import AuditLogItem, AuditLogResponse
from backend.src.schemas.pagination import KeysetPageInfo


def encode_cursor(created_at: datetime, item_id: uuid.UUID) -> str:
    """Encodes created_at timestamp and UUID into an opaque Base64 keyset cursor."""
    payload = {
        "created_at": created_at.isoformat(),
        "id": str(item_id),
    }
    raw = json.dumps(payload).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8")


def decode_cursor(cursor_str: str) -> Tuple[datetime, uuid.UUID]:
    """Decodes an opaque Base64 keyset cursor into (created_at, uuid)."""
    raw = base64.urlsafe_b64decode(cursor_str.encode("utf-8")).decode("utf-8")
    payload = json.loads(raw)
    val = datetime.fromisoformat(payload["created_at"])
    return val, uuid.UUID(payload["id"])


class AuditQueryService:
    """
    Enterprise Keyset-Paginated Audit Query Service.
    Enforces deterministic keyset pagination (created_at DESC, id DESC),
    multi-column filtering (actor, entity type, entity ID, action, timestamp range),
    and partition-pruned query execution using composite B-Tree indexes.
    """

    @classmethod
    async def query_audit_logs(
        cls,
        session: AsyncSession,
        entity_type: Optional[Union[EntityType, str]] = None,
        entity_id: Optional[Union[uuid.UUID, str]] = None,
        actor_id: Optional[Union[uuid.UUID, str]] = None,
        actor_username: Optional[str] = None,
        action: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> AuditLogResponse:
        """
        Executes partition-pruned, keyset-paginated query against audit_logs.
        Returns AuditLogResponse adhering to the OpenAPI schema.
        """
        bounded_limit = max(1, min(limit, 200))
        stmt = select(AuditLog)
        filters = []

        if entity_type is not None:
            resolved_et = (
                EntityType(entity_type)
                if isinstance(entity_type, str)
                else entity_type
            )
            filters.append(AuditLog.entity_type == resolved_et)

        if entity_id is not None:
            resolved_eid = (
                uuid.UUID(entity_id) if isinstance(entity_id, str) else entity_id
            )
            filters.append(AuditLog.entity_id == resolved_eid)

        if actor_id is not None:
            resolved_aid = (
                uuid.UUID(actor_id) if isinstance(actor_id, str) else actor_id
            )
            filters.append(AuditLog.actor_id == resolved_aid)

        if actor_username is not None:
            filters.append(AuditLog.actor_username == actor_username)

        if action is not None:
            filters.append(AuditLog.action == action)

        if start_time is not None:
            filters.append(AuditLog.created_at >= start_time)

        if end_time is not None:
            filters.append(AuditLog.created_at <= end_time)

        if cursor is not None:
            cursor_dt, cursor_uuid = decode_cursor(cursor)
            filters.append(
                or_(
                    AuditLog.created_at < cursor_dt,
                    and_(
                        AuditLog.created_at == cursor_dt,
                        AuditLog.id < cursor_uuid,
                    ),
                )
            )

        if filters:
            stmt = stmt.where(and_(*filters))

        # Deterministic keyset sort
        stmt = stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(
            bounded_limit + 1
        )

        result = await session.execute(stmt)
        records = list(result.scalars().all())

        has_next_page = len(records) > bounded_limit
        items = records[:bounded_limit]

        next_cursor: Optional[str] = None
        if has_next_page and items:
            next_cursor = encode_cursor(items[-1].created_at, items[-1].id)

        items_dto = [AuditLogItem.model_validate(r) for r in items]
        page_info = KeysetPageInfo(
            has_next_page=has_next_page,
            next_cursor=next_cursor,
            limit=bounded_limit,
        )

        return AuditLogResponse(items=items_dto, page_info=page_info)


audit_query_service = AuditQueryService()

