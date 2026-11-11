from __future__ import annotations

import uuid
from datetime import datetime, timezone
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.database import get_db_session
from backend.src.main import app
from backend.src.models.identity import Role, User, UserRole, UserStatus
from backend.src.models.rbac import (
    EntityType,
    PermissionAction,
    PermissionEffect,
    PermissionRule,
    ResourceType,
)
from backend.src.services.audit.audit_service import record_audit_event
from backend.src.services.auth.jwt_service import auth_service


@pytest.fixture
async def audit_api_data(test_db_session: AsyncSession):
    """Sets up an admin user, an auditor user with VIEW_AUDIT_LOGS, and an unprivileged user."""
    suffix = uuid.uuid4().hex[:8]

    # Create auditor role with VIEW_AUDIT_LOGS permission
    auditor_role = Role(
        id=uuid.uuid4(),
        name=f"AuditorRole_{suffix}",
        description="Auditor role for contract testing",
        is_system_role=False,
    )
    test_db_session.add(auditor_role)

    # Create unprivileged role without VIEW_AUDIT_LOGS
    plain_role = Role(
        id=uuid.uuid4(),
        name=f"PlainRole_{suffix}",
        description="Plain role without audit view",
        is_system_role=False,
    )
    test_db_session.add(plain_role)
    await test_db_session.flush()

    # Grant VIEW_AUDIT_LOGS to auditor role
    perm_rule = PermissionRule(
        id=uuid.uuid4(),
        role_id=auditor_role.id,
        resource_type=ResourceType.GLOBAL,
        resource_id=None,
        action=PermissionAction.VIEW_AUDIT_LOGS,
        effect=PermissionEffect.ALLOW,
    )
    test_db_session.add(perm_rule)
    await test_db_session.flush()

    # 1. Superadmin User
    admin = User(
        id=uuid.uuid4(),
        username=f"admin_audit_{suffix}",
        email=f"admin_audit_{suffix}@company.internal",
        hashed_password=auth_service.hash_password("AdminPass123!"),
        full_name="Admin Audit",
        status=UserStatus.ACTIVE,
        is_superadmin=True,
    )
    test_db_session.add(admin)

    # 2. Auditor User
    auditor = User(
        id=uuid.uuid4(),
        username=f"auditor_{suffix}",
        email=f"auditor_{suffix}@company.internal",
        hashed_password=auth_service.hash_password("AuditorPass123!"),
        full_name="Auditor User",
        status=UserStatus.ACTIVE,
        is_superadmin=False,
    )
    test_db_session.add(auditor)
    await test_db_session.flush()

    # Link auditor to auditor role
    test_db_session.add(UserRole(user_id=auditor.id, role_id=auditor_role.id))

    # 3. Unprivileged User
    plain_user = User(
        id=uuid.uuid4(),
        username=f"plain_{suffix}",
        email=f"plain_{suffix}@company.internal",
        hashed_password=auth_service.hash_password("PlainPass123!"),
        full_name="Plain User",
        status=UserStatus.ACTIVE,
        is_superadmin=False,
    )
    test_db_session.add(plain_user)
    await test_db_session.flush()

    # Link plain user to plain role
    test_db_session.add(UserRole(user_id=plain_user.id, role_id=plain_role.id))
    await test_db_session.flush()

    admin_token = auth_service.create_access_token(
        data={
            "sub": str(admin.id),
            "username": admin.username,
            "email": admin.email,
            "roles": ["SuperAdmin"],
            "is_superadmin": True,
        }
    )

    auditor_token = auth_service.create_access_token(
        data={
            "sub": str(auditor.id),
            "username": auditor.username,
            "email": auditor.email,
            "roles": [auditor_role.name],
            "is_superadmin": False,
        }
    )

    plain_token = auth_service.create_access_token(
        data={
            "sub": str(plain_user.id),
            "username": plain_user.username,
            "email": plain_user.email,
            "roles": [plain_role.name],
            "is_superadmin": False,
        }
    )

    return {
        "admin": admin,
        "auditor": auditor,
        "plain_user": plain_user,
        "admin_headers": {"Authorization": f"Bearer {admin_token}"},
        "auditor_headers": {"Authorization": f"Bearer {auditor_token}"},
        "plain_headers": {"Authorization": f"Bearer {plain_token}"},
        "suffix": suffix,
    }


@pytest.mark.asyncio
async def test_audit_logs_unauthenticated_returns_401():
    """Asserts unauthenticated GET /api/v1/audit-logs returns 401 Unauthorized."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/audit-logs")
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_audit_logs_forbidden_without_view_permission(
    test_db_session: AsyncSession, audit_api_data: dict
):
    """
    Asserts that an authenticated user lacking VIEW_AUDIT_LOGS receives 403 Forbidden.
    """
    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/v1/audit-logs",
                headers=audit_api_data["plain_headers"],
            )
            assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_audit_logs_authorized_auditor_returns_200_and_schema(
    test_db_session: AsyncSession, audit_api_data: dict
):
    """
    Asserts that an auditor with VIEW_AUDIT_LOGS receives 200 OK adhering to
    the OpenAPI AuditLogResponse contract.
    """
    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/v1/audit-logs",
                headers=audit_api_data["auditor_headers"],
            )
            assert response.status_code == 200
            data = response.json()
            assert "items" in data
            assert isinstance(data["items"], list)
            assert "page_info" in data
            assert "has_next_page" in data["page_info"]
            assert "next_cursor" in data["page_info"]
            assert "limit" in data["page_info"]
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_audit_logs_filtering_contract(
    test_db_session: AsyncSession, audit_api_data: dict
):
    """
    Asserts that GET /api/v1/audit-logs filters correctly by:
    - entity_type and entity_id
    - actor_id
    - action
    - date range (start_time, end_time)
    """
    entity_a = uuid.uuid4()
    entity_b = uuid.uuid4()
    auditor = audit_api_data["auditor"]
    admin = audit_api_data["admin"]

    # Seed distinct audit records
    rec1 = await record_audit_event(
        session=test_db_session,
        actor_username=auditor.username,
        action="UPDATE_SCHEMA",
        entity_type=EntityType.TABLE,
        entity_id=entity_a,
        actor_id=auditor.id,
        created_at=datetime(2026, 11, 10, 10, 0, 0, tzinfo=timezone.utc),
    )
    rec2 = await record_audit_event(
        session=test_db_session,
        actor_username=admin.username,
        action="CREATE_TABLE",
        entity_type=EntityType.TABLE,
        entity_id=entity_b,
        actor_id=admin.id,
        created_at=datetime(2026, 11, 10, 11, 0, 0, tzinfo=timezone.utc),
    )
    rec3 = await record_audit_event(
        session=test_db_session,
        actor_username=admin.username,
        action="DROP_COLUMN",
        entity_type=EntityType.COLUMN,
        entity_id=entity_b,
        actor_id=admin.id,
        created_at=datetime(2026, 11, 10, 12, 0, 0, tzinfo=timezone.utc),
    )
    await test_db_session.commit()

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = audit_api_data["admin_headers"]

            # Filter by entity_type=TABLE and entity_id=entity_a
            res_entity = await client.get(
                f"/api/v1/audit-logs?entity_type=TABLE&entity_id={entity_a}",
                headers=headers,
            )
            assert res_entity.status_code == 200
            data_e = res_entity.json()
            assert len(data_e["items"]) >= 1
            assert all(i["entity_id"] == str(entity_a) for i in data_e["items"])
            assert all(i["entity_type"] == "TABLE" for i in data_e["items"])

            # Filter by actor_id=auditor.id
            res_actor = await client.get(
                f"/api/v1/audit-logs?actor_id={auditor.id}",
                headers=headers,
            )
            assert res_actor.status_code == 200
            data_a = res_actor.json()
            assert len(data_a["items"]) >= 1
            assert all(i["actor_id"] == str(auditor.id) for i in data_a["items"])

            # Filter by action=CREATE_TABLE
            res_action = await client.get(
                "/api/v1/audit-logs?action=CREATE_TABLE",
                headers=headers,
            )
            assert res_action.status_code == 200
            data_act = res_action.json()
            assert any(i["id"] == str(rec2.id) for i in data_act["items"])
            assert all(i["action"] == "CREATE_TABLE" for i in data_act["items"])

            # Filter by date range (10:30 to 11:30 -> only rec2)
            start_iso = "2026-11-10T10:30:00Z"
            end_iso = "2026-11-10T11:30:00Z"
            res_date = await client.get(
                f"/api/v1/audit-logs?start_time={start_iso}&end_time={end_iso}",
                headers=headers,
            )
            assert res_date.status_code == 200
            data_date = res_date.json()
            item_ids = [i["id"] for i in data_date["items"]]
            assert str(rec2.id) in item_ids
            assert str(rec1.id) not in item_ids
            assert str(rec3.id) not in item_ids
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_audit_logs_keyset_pagination_contract(
    test_db_session: AsyncSession, audit_api_data: dict
):
    """
    Asserts deterministic keyset pagination on GET /api/v1/audit-logs:
    - limit query parameter
    - page_info.next_cursor and has_next_page
    - sequential non-overlapping pages
    """
    target_entity = uuid.uuid4()
    admin = audit_api_data["admin"]

    # Seed 5 records with sequential timestamps
    for i in range(5):
        await record_audit_event(
            session=test_db_session,
            actor_username=admin.username,
            action=f"STEP_{i}",
            entity_type=EntityType.TABLE,
            entity_id=target_entity,
            actor_id=admin.id,
            diff_jsonb={"idx": i},
            created_at=datetime(2026, 11, 10, 15, i, 0, tzinfo=timezone.utc),
        )
    await test_db_session.commit()

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = audit_api_data["admin_headers"]

            # Page 1: limit=2
            res1 = await client.get(
                f"/api/v1/audit-logs?entity_id={target_entity}&limit=2",
                headers=headers,
            )
            assert res1.status_code == 200
            data1 = res1.json()
            assert len(data1["items"]) == 2
            assert data1["page_info"]["has_next_page"] is True
            cursor1 = data1["page_info"]["next_cursor"]
            assert cursor1 is not None

            # Page 2: limit=2, cursor=cursor1
            res2 = await client.get(
                f"/api/v1/audit-logs?entity_id={target_entity}&limit=2&cursor={cursor1}",
                headers=headers,
            )
            assert res2.status_code == 200
            data2 = res2.json()
            assert len(data2["items"]) == 2
            assert data2["page_info"]["has_next_page"] is True
            cursor2 = data2["page_info"]["next_cursor"]
            assert cursor2 is not None

            # Page 3: limit=2, cursor=cursor2
            res3 = await client.get(
                f"/api/v1/audit-logs?entity_id={target_entity}&limit=2&cursor={cursor2}",
                headers=headers,
            )
            assert res3.status_code == 200
            data3 = res3.json()
            assert len(data3["items"]) == 1
            assert data3["page_info"]["has_next_page"] is False

            # Verify no duplicates across pages
            p1_ids = {i["id"] for i in data1["items"]}
            p2_ids = {i["id"] for i in data2["items"]}
            p3_ids = {i["id"] for i in data3["items"]}
            assert p1_ids.isdisjoint(p2_ids)
            assert p1_ids.isdisjoint(p3_ids)
            assert p2_ids.isdisjoint(p3_ids)
    finally:
        app.dependency_overrides.pop(get_db_session, None)

