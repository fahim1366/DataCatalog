from __future__ import annotations

import uuid
from datetime import datetime, timezone
import pytest
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.database import async_session_factory, engine
from backend.src.models.audit import AuditLog
from backend.src.models.identity import User, UserStatus
from backend.src.models.rbac import EntityType
from backend.src.services.audit.audit_query_service import AuditQueryService
from backend.src.services.audit.audit_service import record_audit_event


@pytest.mark.asyncio
async def test_audit_logs_immutability_update_blocked():
    """
    Assert that direct SQL UPDATE on audit_logs is blocked by PostgreSQL
    immutability rule (audit_logs_no_update). The UPDATE statement affects 0 rows
    and leaves the audit data unchanged.
    """
    event_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    async with async_session_factory() as session:
        user = User(
            id=uuid.uuid4(),
            username=f"immut_user_{uuid.uuid4().hex[:8]}",
            email=f"immut_{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="secure_hash_pwd",
            full_name="Immutability Tester",
            status=UserStatus.ACTIVE,
        )
        session.add(user)
        await session.flush()

        await record_audit_event(
            session=session,
            actor_username=user.username,
            action="ORIGINAL_ACTION",
            entity_type=EntityType.TABLE,
            entity_id=entity_id,
            actor_id=user.id,
            diff_jsonb={"field": "status", "old": "DRAFT", "new": "ACTIVE"},
            event_id=event_id,
            created_at=datetime(2026, 11, 10, 10, 0, 0, tzinfo=timezone.utc),
        )
        await session.commit()

    # Attempt direct SQL UPDATE on audit_logs
    async with async_session_factory() as session:
        result = await session.execute(
            text(
                "UPDATE audit_logs SET actor_username = 'hacked', action = 'TAMPERED' WHERE id = :id"
            ),
            {"id": event_id},
        )
        await session.commit()
        # Due to DO INSTEAD NOTHING rule, affected rows must be 0
        assert result.rowcount in (0, -1)

    # Verify data in audit_logs was not modified
    async with async_session_factory() as session:
        stmt = select(AuditLog).where(AuditLog.id == event_id)
        res = await session.execute(stmt)
        record = res.scalar_one()
        assert record.actor_username != "hacked"
        assert record.action == "ORIGINAL_ACTION"


@pytest.mark.asyncio
async def test_audit_logs_immutability_delete_blocked():
    """
    Assert that direct SQL DELETE on audit_logs is blocked by PostgreSQL
    immutability rule (audit_logs_no_delete). The DELETE statement affects 0 rows
    and leaves the record intact.
    """
    event_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    async with async_session_factory() as session:
        user = User(
            id=uuid.uuid4(),
            username=f"del_user_{uuid.uuid4().hex[:8]}",
            email=f"del_{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="secure_hash_pwd",
            full_name="Delete Tester",
            status=UserStatus.ACTIVE,
        )
        session.add(user)
        await session.flush()

        await record_audit_event(
            session=session,
            actor_username=user.username,
            action="CRITICAL_EVENT",
            entity_type=EntityType.DATABASE,
            entity_id=entity_id,
            actor_id=user.id,
            event_id=event_id,
            created_at=datetime(2026, 11, 10, 11, 0, 0, tzinfo=timezone.utc),
        )
        await session.commit()

    # Attempt direct SQL DELETE on audit_logs
    async with async_session_factory() as session:
        result = await session.execute(
            text("DELETE FROM audit_logs WHERE id = :id"),
            {"id": event_id},
        )
        await session.commit()
        assert result.rowcount in (0, -1)

    # Verify record still exists in audit_logs
    async with async_session_factory() as session:
        stmt = select(AuditLog).where(AuditLog.id == event_id)
        res = await session.execute(stmt)
        record = res.scalar_one_or_none()
        assert record is not None
        assert record.action == "CRITICAL_EVENT"


@pytest.mark.asyncio
async def test_audit_log_partition_pruning_via_explain():
    """
    Assert that date-bounded queries against the partitioned audit_logs table
    prune non-matching monthly partitions in PostgreSQL query plans.
    """
    async with engine.connect() as conn:
        # Date range fully contained in 2026-11
        explain_nov = await conn.execute(
            text(
                "EXPLAIN (FORMAT TEXT) "
                "SELECT * FROM audit_logs "
                "WHERE created_at >= '2026-11-05 00:00:00+00' AND created_at < '2026-11-20 00:00:00+00'"
            )
        )
        plan_nov_lines = [r[0] for r in explain_nov.fetchall()]
        plan_nov_text = "\n".join(plan_nov_lines)

        # Must scan the 2026-11 partition
        assert "audit_logs_y2026m11" in plan_nov_text
        # Must prune other month partitions
        assert "audit_logs_y2026m12" not in plan_nov_text
        assert "audit_logs_y2027m01" not in plan_nov_text
        assert "audit_logs_y2027m02" not in plan_nov_text

        # Date range fully contained in 2026-12
        explain_dec = await conn.execute(
            text(
                "EXPLAIN (FORMAT TEXT) "
                "SELECT * FROM audit_logs "
                "WHERE created_at >= '2026-12-05 00:00:00+00' AND created_at < '2026-12-20 00:00:00+00'"
            )
        )
        plan_dec_lines = [r[0] for r in explain_dec.fetchall()]
        plan_dec_text = "\n".join(plan_dec_lines)

        # Must scan 2026-12 partition
        assert "audit_logs_y2026m12" in plan_dec_text
        # Must prune other month partitions
        assert "audit_logs_y2026m11" not in plan_dec_text
        assert "audit_logs_y2027m01" not in plan_dec_text
        assert "audit_logs_y2027m02" not in plan_dec_text


@pytest.mark.asyncio
async def test_audit_query_service_indexes_and_partition_pruning():
    """
    Assert that queries by entity and actor leverage the composite B-Tree indexes
    (idx_audit_logs_entity_created and idx_audit_logs_actor_created) and prune partitions
    when timestamp bounds are specified.
    """
    target_entity_id = uuid.uuid4()
    actor_id = uuid.uuid4()

    async with engine.connect() as conn:
        # Check entity_created index and pruning
        explain_entity = await conn.execute(
            text(
                "EXPLAIN (FORMAT TEXT) "
                "SELECT * FROM audit_logs "
                "WHERE entity_type = 'TABLE' AND entity_id = :eid "
                "AND created_at >= '2026-11-01 00:00:00+00' AND created_at < '2026-12-01 00:00:00+00' "
                "ORDER BY created_at DESC, id DESC"
            ),
            {"eid": target_entity_id},
        )
        plan_entity_text = "\n".join([r[0] for r in explain_entity.fetchall()])
        assert "audit_logs_y2026m11" in plan_entity_text
        assert "audit_logs_y2026m12" not in plan_entity_text
        assert "entity_type_entity_id_created_at" in plan_entity_text or "idx_audit_logs_entity_created" in plan_entity_text

        # Check actor_created index and pruning
        explain_actor = await conn.execute(
            text(
                "EXPLAIN (FORMAT TEXT) "
                "SELECT * FROM audit_logs "
                "WHERE actor_id = :aid "
                "AND created_at >= '2026-11-01 00:00:00+00' AND created_at < '2026-12-01 00:00:00+00' "
                "ORDER BY created_at DESC, id DESC"
            ),
            {"aid": actor_id},
        )
        plan_actor_text = "\n".join([r[0] for r in explain_actor.fetchall()])
        assert "audit_logs_y2026m11" in plan_actor_text
        assert "audit_logs_y2026m12" not in plan_actor_text
        assert "actor_id_created_at" in plan_actor_text or "idx_audit_logs_actor_created" in plan_actor_text


@pytest.mark.asyncio
async def test_audit_query_service_deterministic_keyset_pagination():
    """
    Assert that AuditQueryService provides deterministic keyset cursor pagination
    ordered by created_at DESC, id DESC.
    """
    entity_id = uuid.uuid4()
    actor_id = uuid.uuid4()

    async with async_session_factory() as session:
        user = User(
            id=actor_id,
            username=f"page_user_{uuid.uuid4().hex[:8]}",
            email=f"page_{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="secure_hash_pwd",
            full_name="Pagination Tester",
            status=UserStatus.ACTIVE,
        )
        session.add(user)
        await session.flush()

        # Seed 6 audit records with distinct timestamps
        created_records = []
        for i in range(6):
            rec = await record_audit_event(
                session=session,
                actor_username=user.username,
                action="UPDATE_ROW",
                entity_type=EntityType.TABLE,
                entity_id=entity_id,
                actor_id=user.id,
                diff_jsonb={"step": i},
                created_at=datetime(2026, 11, 10, 14, 10 + i, 0, tzinfo=timezone.utc),
            )
            created_records.append(rec)
        await session.commit()

    async with async_session_factory() as session:
        # Fetch Page 1 (limit 2)
        page1 = await AuditQueryService.query_audit_logs(
            session=session,
            entity_type=EntityType.TABLE,
            entity_id=entity_id,
            limit=2,
        )
        assert len(page1.items) == 2
        assert page1.page_info.has_next_page is True
        assert page1.page_info.next_cursor is not None

        # Fetch Page 2 (limit 2)
        page2 = await AuditQueryService.query_audit_logs(
            session=session,
            entity_type=EntityType.TABLE,
            entity_id=entity_id,
            limit=2,
            cursor=page1.page_info.next_cursor,
        )
        assert len(page2.items) == 2
        assert page2.page_info.has_next_page is True
        assert page2.page_info.next_cursor is not None

        # Fetch Page 3 (limit 2)
        page3 = await AuditQueryService.query_audit_logs(
            session=session,
            entity_type=EntityType.TABLE,
            entity_id=entity_id,
            limit=2,
            cursor=page2.page_info.next_cursor,
        )
        assert len(page3.items) == 2
        assert page3.page_info.has_next_page is False

        # Validate deterministic sorting (created_at DESC) and disjoint items
        page1_ids = [item.id for item in page1.items]
        page2_ids = [item.id for item in page2.items]
        page3_ids = [item.id for item in page3.items]

        all_paged_ids = page1_ids + page2_ids + page3_ids
        assert len(set(all_paged_ids)) == 6
        assert page1.items[0].created_at >= page1.items[1].created_at
        assert page1.items[1].created_at >= page2.items[0].created_at

