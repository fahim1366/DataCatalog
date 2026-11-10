from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

from sqlalchemy import and_, event, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from backend.src.models.audit import AuditLog
from backend.src.models.rbac import EntityType


def _session_before_flush_listener(
    session: Session, flush_context: Any, instances: Any
) -> None:
    """SQLAlchemy before_flush hook that appends queued audit entries to the session."""
    pending: Optional[List[AuditLog]] = session.info.get("pending_audit_events")
    if pending:
        while pending:
            entry = pending.pop(0)
            session.add(entry)


def _encode_cursor(created_at: datetime, item_id: uuid.UUID) -> str:
    """Encodes created_at timestamp and entity UUID into an opaque Base64 keyset cursor."""
    payload = {
        "val": created_at.isoformat(),
        "id": str(item_id),
    }
    raw = json.dumps(payload).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8")


def _decode_cursor(cursor_str: str) -> Tuple[datetime, uuid.UUID]:
    """Decodes an opaque Base64 keyset cursor into (created_at, uuid)."""
    raw = base64.urlsafe_b64decode(cursor_str.encode("utf-8")).decode("utf-8")
    payload = json.loads(raw)
    val = datetime.fromisoformat(payload["val"])
    return val, uuid.UUID(payload["id"])


async def record_audit_event(
    session: AsyncSession,
    actor_username: str,
    action: str,
    entity_type: Union[EntityType, str],
    entity_id: Union[uuid.UUID, str],
    actor_id: Optional[Union[uuid.UUID, str]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    diff_jsonb: Optional[Dict[str, Any]] = None,
    event_id: Optional[uuid.UUID] = None,
    created_at: Optional[datetime] = None,
    flush: bool = False,
) -> AuditLog:
    """
    Persists an immutable AuditLog record in the active database transaction.
    Automatically assigns to current month partition based on created_at timestamp.
    Guarantees strict atomicity: rolls back if surrounding transaction fails.
    """
    # Normalize entity_type
    if isinstance(entity_type, str):
        resolved_entity_type = EntityType(entity_type)
    else:
        resolved_entity_type = entity_type

    # Normalize UUIDs
    resolved_entity_id = (
        uuid.UUID(entity_id) if isinstance(entity_id, str) else entity_id
    )
    resolved_actor_id = (
        uuid.UUID(actor_id)
        if isinstance(actor_id, str)
        else actor_id
    )

    log_entry = AuditLog(
        id=event_id or uuid.uuid4(),
        created_at=created_at or datetime.now(timezone.utc),
        actor_id=resolved_actor_id,
        actor_username=actor_username,
        ip_address=ip_address,
        user_agent=user_agent,
        action=action,
        entity_type=resolved_entity_type,
        entity_id=resolved_entity_id,
        diff_jsonb=diff_jsonb if diff_jsonb is not None else {},
    )

    session.add(log_entry)

    if flush:
        await session.flush()

    return log_entry


class AuditService:
    """
    Core Enterprise Audit Logging Service providing session flush hooks,
    transactional event recording, and keyset-paginated audit trail queries.
    """

    @classmethod
    def attach_session_hook(cls, session: Union[AsyncSession, Session]) -> None:
        """
        Attaches the before_flush audit listener to the provided session.
        Idempotent: will not attach multiple duplicate listeners to the same session.
        """
        sync_sess: Session = (
            session.sync_session
            if isinstance(session, AsyncSession)
            else session
        )
        if not sync_sess.info.get("_audit_hook_attached"):
            event.listen(sync_sess, "before_flush", _session_before_flush_listener)
            sync_sess.info["_audit_hook_attached"] = True

    @classmethod
    def queue_audit_event(
        cls,
        session: Union[AsyncSession, Session],
        actor_username: str,
        action: str,
        entity_type: Union[EntityType, str],
        entity_id: Union[uuid.UUID, str],
        actor_id: Optional[Union[uuid.UUID, str]] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        diff_jsonb: Optional[Dict[str, Any]] = None,
        event_id: Optional[uuid.UUID] = None,
        created_at: Optional[datetime] = None,
    ) -> AuditLog:
        """
        Queues an AuditLog to be materialized during the next session.flush()
        via the attached session before_flush listener.
        """
        cls.attach_session_hook(session)
        sync_sess: Session = (
            session.sync_session
            if isinstance(session, AsyncSession)
            else session
        )

        # Normalize entity_type
        if isinstance(entity_type, str):
            resolved_entity_type = EntityType(entity_type)
        else:
            resolved_entity_type = entity_type

        # Normalize UUIDs
        resolved_entity_id = (
            uuid.UUID(entity_id) if isinstance(entity_id, str) else entity_id
        )
        resolved_actor_id = (
            uuid.UUID(actor_id)
            if isinstance(actor_id, str)
            else actor_id
        )

        log_entry = AuditLog(
            id=event_id or uuid.uuid4(),
            created_at=created_at or datetime.now(timezone.utc),
            actor_id=resolved_actor_id,
            actor_username=actor_username,
            ip_address=ip_address,
            user_agent=user_agent,
            action=action,
            entity_type=resolved_entity_type,
            entity_id=resolved_entity_id,
            diff_jsonb=diff_jsonb if diff_jsonb is not None else {},
        )

        sync_sess.add(log_entry)
        pending_list: List[AuditLog] = sync_sess.info.setdefault(
            "pending_audit_events", []
        )
        pending_list.append(log_entry)
        return log_entry

    @classmethod
    async def record_event(
        cls,
        session: AsyncSession,
        actor_username: str,
        action: str,
        entity_type: Union[EntityType, str],
        entity_id: Union[uuid.UUID, str],
        actor_id: Optional[Union[uuid.UUID, str]] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        diff_jsonb: Optional[Dict[str, Any]] = None,
        event_id: Optional[uuid.UUID] = None,
        created_at: Optional[datetime] = None,
        flush: bool = False,
    ) -> AuditLog:
        """Delegates directly to record_audit_event."""
        return await record_audit_event(
            session=session,
            actor_username=actor_username,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            actor_id=actor_id,
            ip_address=ip_address,
            user_agent=user_agent,
            diff_jsonb=diff_jsonb,
            event_id=event_id,
            created_at=created_at,
            flush=flush,
        )

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
    ) -> Dict[str, Any]:
        """
        Executes partition-pruned, keyset-paginated query against audit_logs.
        Complies strictly with OpenAPI KeysetPageInfo and AuditLogResponse schema.
        """
        bounded_limit = max(1, min(limit, 200))
        stmt = select(AuditLog)

        filters = []

        if entity_type is not None:
            res_et = (
                EntityType(entity_type)
                if isinstance(entity_type, str)
                else entity_type
            )
            filters.append(AuditLog.entity_type == res_et)

        if entity_id is not None:
            res_eid = (
                uuid.UUID(entity_id) if isinstance(entity_id, str) else entity_id
            )
            filters.append(AuditLog.entity_id == res_eid)

        if actor_id is not None:
            res_aid = (
                uuid.UUID(actor_id) if isinstance(actor_id, str) else actor_id
            )
            filters.append(AuditLog.actor_id == res_aid)

        if actor_username is not None:
            filters.append(AuditLog.actor_username == actor_username)

        if action is not None:
            filters.append(AuditLog.action == action)

        if start_time is not None:
            filters.append(AuditLog.created_at >= start_time)

        if end_time is not None:
            filters.append(AuditLog.created_at <= end_time)

        if cursor is not None:
            cursor_dt, cursor_uuid = _decode_cursor(cursor)
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

        stmt = stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(
            bounded_limit + 1
        )

        result = await session.execute(stmt)
        records = list(result.scalars().all())

        has_next_page = len(records) > bounded_limit
        items = records[:bounded_limit]

        end_cursor: Optional[str] = None
        if items:
            end_cursor = _encode_cursor(items[-1].created_at, items[-1].id)

        items_payload = [
            {
                "id": str(r.id),
                "actor_id": str(r.actor_id) if r.actor_id else None,
                "actor_username": r.actor_username,
                "ip_address": str(r.ip_address) if r.ip_address else None,
                "user_agent": r.user_agent,
                "action": r.action,
                "entity_type": (
                    r.entity_type.value
                    if hasattr(r.entity_type, "value")
                    else str(r.entity_type)
                ),
                "entity_id": str(r.entity_id),
                "diff_jsonb": r.diff_jsonb,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in items
        ]

        return {
            "items": items_payload,
            "page_info": {
                "has_next_page": has_next_page,
                "end_cursor": end_cursor,
            },
        }
