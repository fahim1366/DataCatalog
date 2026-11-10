import uuid
from datetime import datetime, timezone
import pytest
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.database import async_session_factory, engine
from backend.src.models.audit import AuditLog
from backend.src.models.rbac import EntityType
from backend.src.models.identity import Role, User, UserStatus
from backend.src.services.audit.audit_service import (
    AuditService,
    record_audit_event,
)


@pytest.mark.asyncio
async def test_record_audit_event_persists_in_current_month_partition():
    """
    Assert that record_audit_event persists an AuditLog into the partitioned audit_logs table
    and routes physically into the current month partition (audit_logs_y2026m11).
    """
    event_id = uuid.uuid4()
    entity_id = uuid.uuid4()
    diff_payload = {"field": "approved_description", "old": "Old Text", "new": "New Text"}

    async with async_session_factory() as session:
        # Create valid user for actor_id foreign key
        test_user = User(
            id=uuid.uuid4(),
            username=f"auditor_{uuid.uuid4().hex[:8]}",
            email=f"auditor_{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="secure_hash_pwd",
            full_name="Audit Tester",
            status=UserStatus.ACTIVE,
        )
        session.add(test_user)
        await session.flush()

        # Record audit log in active transaction
        audit_log = await record_audit_event(
            session=session,
            actor_username=test_user.username,
            action="EDIT_DESCRIPTION",
            entity_type=EntityType.TABLE,
            entity_id=entity_id,
            actor_id=test_user.id,
            ip_address="192.168.1.100",
            user_agent="pytest-agent/1.0",
            diff_jsonb=diff_payload,
            event_id=event_id,
            created_at=datetime(2026, 11, 10, 12, 0, 0, tzinfo=timezone.utc),
        )
        assert audit_log is not None
        assert audit_log.id == event_id
        await session.commit()

    # Query via main partitioned table
    async with async_session_factory() as verify_session:
        stmt = select(AuditLog).where(AuditLog.id == event_id)
        result = await verify_session.execute(stmt)
        persisted = result.scalar_one_or_none()
        assert persisted is not None
        assert persisted.actor_username == test_user.username
        assert persisted.actor_id == test_user.id
        assert persisted.action == "EDIT_DESCRIPTION"
        assert persisted.entity_type == EntityType.TABLE
        assert persisted.entity_id == entity_id
        assert str(persisted.ip_address) == "192.168.1.100"
        assert persisted.user_agent == "pytest-agent/1.0"
        assert persisted.diff_jsonb == diff_payload

    # Direct query against the physical partition table audit_logs_y2026m11
    async with engine.connect() as conn:
        partition_check = await conn.execute(
            text("SELECT count(*) FROM audit_logs_y2026m11 WHERE id = :id"),
            {"id": event_id},
        )
        count = partition_check.scalar_one()
        assert count == 1, "AuditLog record was not routed into the 2026-11 monthly partition table!"


@pytest.mark.asyncio
async def test_record_audit_event_rollback_on_transaction_failure():
    """
    Assert that if the active database transaction fails or is rolled back,
    the AuditLog record is rolled back cleanly (strict atomicity guarantee).
    """
    event_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    async with async_session_factory() as session:
        await record_audit_event(
            session=session,
            actor_username="rollback_user",
            action="UNAUTHORIZED_ACCESS",
            entity_type=EntityType.DATABASE,
            entity_id=entity_id,
            diff_jsonb={"reason": "forbidden"},
            event_id=event_id,
            created_at=datetime(2026, 11, 10, 12, 5, 0, tzinfo=timezone.utc),
        )
        # Explicit rollback simulating an unhandled error or failed transaction
        await session.rollback()

    # Verify no record was persisted
    async with async_session_factory() as verify_session:
        stmt = select(AuditLog).where(AuditLog.id == event_id)
        result = await verify_session.execute(stmt)
        assert result.scalar_one_or_none() is None

    # Verify physical partition also has 0 records
    async with engine.connect() as conn:
        partition_check = await conn.execute(
            text("SELECT count(*) FROM audit_logs_y2026m11 WHERE id = :id"),
            {"id": event_id},
        )
        assert partition_check.scalar_one() == 0


@pytest.mark.asyncio
async def test_record_audit_event_atomic_with_domain_mutation():
    """
    Assert that domain state mutation and audit log persist atomically in the same transaction.
    If the transaction succeeds, both exist; if it rolls back, neither exists.
    """
    role_id = uuid.uuid4()
    audit_id = uuid.uuid4()
    role_name = f"AuditedRole_{uuid.uuid4().hex[:8]}"

    # 1. Successful transaction: both saved
    async with async_session_factory() as session:
        new_role = Role(id=role_id, name=role_name, description="Test audited role")
        session.add(new_role)

        await record_audit_event(
            session=session,
            actor_username="admin_user",
            action="CREATE_ROLE",
            entity_type=EntityType.DATABASE,
            entity_id=role_id,
            diff_jsonb={"role_name": role_name},
            event_id=audit_id,
            created_at=datetime(2026, 11, 10, 12, 10, 0, tzinfo=timezone.utc),
        )
        await session.commit()

    async with async_session_factory() as verify_session:
        role_res = await verify_session.get(Role, role_id)
        audit_res = await verify_session.execute(select(AuditLog).where(AuditLog.id == audit_id))
        assert role_res is not None
        assert audit_res.scalar_one_or_none() is not None

    # 2. Failed transaction: neither saved
    failed_role_id = uuid.uuid4()
    failed_audit_id = uuid.uuid4()
    failed_role_name = f"FailedRole_{uuid.uuid4().hex[:8]}"

    async with async_session_factory() as session:
        new_role2 = Role(id=failed_role_id, name=failed_role_name, description="Failed role")
        session.add(new_role2)

        await record_audit_event(
            session=session,
            actor_username="admin_user",
            action="CREATE_ROLE",
            entity_type=EntityType.DATABASE,
            entity_id=failed_role_id,
            diff_jsonb={"role_name": failed_role_name},
            event_id=failed_audit_id,
            created_at=datetime(2026, 11, 10, 12, 11, 0, tzinfo=timezone.utc),
        )
        await session.rollback()

    async with async_session_factory() as verify_session:
        role_res2 = await verify_session.get(Role, failed_role_id)
        audit_res2 = await verify_session.execute(select(AuditLog).where(AuditLog.id == failed_audit_id))
        assert role_res2 is None
        assert audit_res2.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_audit_service_session_flush_hook():
    """
    Assert that AuditService can attach session hooks to automatically flush
    queued audit events upon session.flush() before transaction commit.
    """
    event_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    async with async_session_factory() as session:
        # Attach session hook to active session
        AuditService.attach_session_hook(session)

        # Queue audit event in session context
        AuditService.queue_audit_event(
            session=session,
            actor_username="hook_auditor",
            action="MANAGE_TAGS",
            entity_type=EntityType.COLUMN,
            entity_id=entity_id,
            diff_jsonb={"tag": "PII"},
            event_id=event_id,
            created_at=datetime(2026, 11, 10, 12, 15, 0, tzinfo=timezone.utc),
        )

        # Trigger flush
        await session.flush()

        # Commit transaction
        await session.commit()

    # Verify event was persisted via the flush hook
    async with async_session_factory() as verify_session:
        stmt = select(AuditLog).where(AuditLog.id == event_id)
        res = await verify_session.execute(stmt)
        persisted = res.scalar_one_or_none()
        assert persisted is not None
        assert persisted.actor_username == "hook_auditor"
        assert persisted.action == "MANAGE_TAGS"


@pytest.mark.asyncio
async def test_audit_log_immutability_enforcement():
    """
    Assert that the PostgreSQL immutability rules prevent UPDATE and DELETE
    on audit log entries (rules audit_logs_no_update and audit_logs_no_delete).
    """
    event_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    async with async_session_factory() as session:
        await record_audit_event(
            session=session,
            actor_username="immutable_actor",
            action="SYSTEM_BOOTSTRAP",
            entity_type=EntityType.DATABASE,
            entity_id=entity_id,
            diff_jsonb={"status": "initial"},
            event_id=event_id,
            created_at=datetime(2026, 11, 10, 12, 20, 0, tzinfo=timezone.utc),
        )
        await session.commit()

    # Attempt UPDATE
    async with async_session_factory() as session:
        await session.execute(
            text("UPDATE audit_logs SET actor_username = 'tampered' WHERE id = :id"),
            {"id": event_id},
        )
        await session.commit()

    # Verify UPDATE did not modify the record
    async with async_session_factory() as session:
        res = await session.execute(
            text("SELECT actor_username FROM audit_logs WHERE id = :id"),
            {"id": event_id},
        )
        assert res.scalar_one() == "immutable_actor"

    # Attempt DELETE
    async with async_session_factory() as session:
        await session.execute(
            text("DELETE FROM audit_logs WHERE id = :id"),
            {"id": event_id},
        )
        await session.commit()

    # Verify record still exists
    async with async_session_factory() as session:
        res = await session.execute(
            text("SELECT count(*) FROM audit_logs WHERE id = :id"),
            {"id": event_id},
        )
        assert res.scalar_one() == 1


@pytest.mark.asyncio
async def test_audit_service_query_and_keyset_pagination():
    """
    Assert that AuditService.query_audit_logs supports multi-column filtering
    and keyset cursor pagination matching the OpenAPI contract.
    """
    target_entity_id = uuid.uuid4()

    # Seed 5 audit records for this entity
    async with async_session_factory() as session:
        for i in range(5):
            await record_audit_event(
                session=session,
                actor_username=f"user_{i}",
                action="EDIT_DESCRIPTION",
                entity_type=EntityType.TABLE,
                entity_id=target_entity_id,
                diff_jsonb={"index": i},
                created_at=datetime(2026, 11, 10, 12, 30 + i, 0, tzinfo=timezone.utc),
            )
        await session.commit()

    # Query page 1 (limit 2)
    async with async_session_factory() as session:
        page_1 = await AuditService.query_audit_logs(
            session=session,
            entity_type=EntityType.TABLE,
            entity_id=target_entity_id,
            limit=2,
        )
        assert len(page_1["items"]) == 2
        assert page_1["page_info"]["has_next_page"] is True
        assert page_1["page_info"]["end_cursor"] is not None

        # Fetch page 2 using cursor
        cursor = page_1["page_info"]["end_cursor"]
        page_2 = await AuditService.query_audit_logs(
            session=session,
            entity_type=EntityType.TABLE,
            entity_id=target_entity_id,
            limit=2,
            cursor=cursor,
        )
        assert len(page_2["items"]) == 2
        assert page_2["page_info"]["has_next_page"] is True

        # Items in page 2 must be different from page 1
        page_1_ids = {item["id"] for item in page_1["items"]}
        page_2_ids = {item["id"] for item in page_2["items"]}
        assert page_1_ids.isdisjoint(page_2_ids)
