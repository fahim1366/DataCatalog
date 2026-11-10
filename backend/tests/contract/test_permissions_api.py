from __future__ import annotations

import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.database import get_db_session
from backend.src.main import app
from backend.src.models.audit import AuditLog
from backend.src.models.identity import Role, User, UserRole, UserStatus
from backend.src.models.rbac import (
    PermissionAction,
    PermissionEffect,
    PermissionRule,
    ResourceType,
)
from backend.src.services.auth.jwt_service import auth_service


@pytest.fixture
async def permissions_api_data(test_db_session: AsyncSession):
    """
    Sets up an admin user, a standard role, and JWT token for permissions API testing.
    """
    unique_suffix = uuid.uuid4().hex[:8]

    # Create Role
    role = Role(
        id=uuid.uuid4(),
        name=f"DataAnalystRole_{unique_suffix}",
        description="Analyst role for permissions API contract testing",
        is_system_role=False,
    )
    test_db_session.add(role)
    await test_db_session.flush()

    # Create Admin User
    admin_user = User(
        id=uuid.uuid4(),
        username=f"admin_{unique_suffix}",
        email=f"admin_{unique_suffix}@company.internal",
        hashed_password=auth_service.hash_password("SuperSecret#2026"),
        full_name="Admin User",
        status=UserStatus.ACTIVE,
        is_superadmin=True,
    )
    test_db_session.add(admin_user)
    await test_db_session.flush()

    # Create Regular User
    regular_user = User(
        id=uuid.uuid4(),
        username=f"regular_{unique_suffix}",
        email=f"regular_{unique_suffix}@company.internal",
        hashed_password=auth_service.hash_password("Regular#2026"),
        full_name="Regular User",
        status=UserStatus.ACTIVE,
        is_superadmin=False,
    )
    test_db_session.add(regular_user)
    await test_db_session.flush()

    # Assign regular user to role
    ur = UserRole(user_id=regular_user.id, role_id=role.id)
    test_db_session.add(ur)
    await test_db_session.flush()

    admin_token = auth_service.create_access_token(
        data={
            "sub": str(admin_user.id),
            "username": admin_user.username,
            "email": admin_user.email,
            "roles": ["SuperAdmin"],
            "is_superadmin": True,
        }
    )

    regular_token = auth_service.create_access_token(
        data={
            "sub": str(regular_user.id),
            "username": regular_user.username,
            "email": regular_user.email,
            "roles": [role.name],
            "is_superadmin": False,
        }
    )

    return {
        "role": role,
        "admin_user": admin_user,
        "regular_user": regular_user,
        "admin_token": admin_token,
        "regular_token": regular_token,
        "suffix": unique_suffix,
    }


@pytest.mark.asyncio
async def test_permissions_api_unauthenticated_returns_401():
    """Asserts that requests without Authorization header return 401 Unauthorized."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/permissions")
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_grant_permission_rule_crud_and_audit(
    test_db_session: AsyncSession, permissions_api_data: dict
):
    """
    T019: Tests POST /api/v1/permissions:
    - Successfully creates permission rule.
    - Returns HTTP 201 with rule payload.
    - Atomically creates an immutable AuditLog record in the active transaction.
    """
    data = permissions_api_data
    token = data["admin_token"]
    role = data["role"]
    headers = {"Authorization": f"Bearer {token}"}
    resource_id = str(uuid.uuid4())

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {
                "role_id": str(role.id),
                "resource_type": "TABLE",
                "resource_id": resource_id,
                "action": "VIEW_METADATA",
                "effect": "ALLOW",
            }
            response = await client.post(
                "/api/v1/permissions", json=payload, headers=headers
            )
            assert response.status_code == 201, response.text
            rule_data = response.json()
            assert "id" in rule_data
            assert rule_data["role_id"] == str(role.id)
            assert rule_data["resource_type"] == "TABLE"
            assert rule_data["resource_id"] == resource_id
            assert rule_data["action"] == "VIEW_METADATA"
            assert rule_data["effect"] == "ALLOW"

            rule_id = uuid.UUID(rule_data["id"])

            # Verify rule exists in DB
            rule_in_db = await test_db_session.get(PermissionRule, rule_id)
            assert rule_in_db is not None
            assert rule_in_db.action == PermissionAction.VIEW_METADATA

            # Verify immutable audit log record was atomically generated
            stmt = select(AuditLog).where(
                AuditLog.action == "GRANT_PERMISSION",
                AuditLog.entity_id == uuid.UUID(resource_id),
            )
            audit_result = await test_db_session.execute(stmt)
            audit_log = audit_result.scalar_one_or_none()
            assert audit_log is not None
            assert audit_log.actor_username == data["admin_user"].username
            assert audit_log.diff_jsonb.get("rule_id") == str(rule_id)
            assert audit_log.diff_jsonb.get("effect") == "ALLOW"

    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_inspect_and_filter_permissions(
    test_db_session: AsyncSession, permissions_api_data: dict
):
    """
    T019: Tests GET /api/v1/permissions with role_id, resource_type, and action filters,
    and GET /api/v1/permissions/roles/{role_id}.
    """
    data = permissions_api_data
    token = data["admin_token"]
    role = data["role"]
    headers = {"Authorization": f"Bearer {token}"}

    # Pre-seed 2 permission rules
    rule1 = PermissionRule(
        id=uuid.uuid4(),
        role_id=role.id,
        resource_type=ResourceType.GLOBAL,
        resource_id=None,
        action=PermissionAction.VIEW_METADATA,
        effect=PermissionEffect.ALLOW,
    )
    rule2 = PermissionRule(
        id=uuid.uuid4(),
        role_id=role.id,
        resource_type=ResourceType.TABLE,
        resource_id=uuid.uuid4(),
        action=PermissionAction.EDIT_DESCRIPTION,
        effect=PermissionEffect.ALLOW,
    )
    test_db_session.add_all([rule1, rule2])
    await test_db_session.flush()

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Filter by role_id
            res = await client.get(
                f"/api/v1/permissions?role_id={role.id}", headers=headers
            )
            assert res.status_code == 200
            items = res.json()
            rule_ids = {r["id"] for r in items}
            assert str(rule1.id) in rule_ids
            assert str(rule2.id) in rule_ids

            # 2. Filter by action
            res_action = await client.get(
                f"/api/v1/permissions?action=EDIT_DESCRIPTION", headers=headers
            )
            assert res_action.status_code == 200
            action_items = res_action.json()
            assert all(r["action"] == "EDIT_DESCRIPTION" for r in action_items)

            # 3. GET /api/v1/permissions/roles/{role_id}
            res_role = await client.get(
                f"/api/v1/permissions/roles/{role.id}", headers=headers
            )
            assert res_role.status_code == 200
            role_rules = res_role.json()
            assert len(role_rules) >= 2

    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_revoke_permission_rule_and_audit(
    test_db_session: AsyncSession, permissions_api_data: dict
):
    """
    T019: Tests DELETE /api/v1/permissions/{rule_id}:
    - Removes permission rule from database.
    - Returns HTTP 204 No Content.
    - Atomically writes REVOKE_PERMISSION audit record.
    """
    data = permissions_api_data
    token = data["admin_token"]
    role = data["role"]
    headers = {"Authorization": f"Bearer {token}"}
    table_id = uuid.uuid4()

    rule = PermissionRule(
        id=uuid.uuid4(),
        role_id=role.id,
        resource_type=ResourceType.TABLE,
        resource_id=table_id,
        action=PermissionAction.MANAGE_TAGS,
        effect=PermissionEffect.ALLOW,
    )
    test_db_session.add(rule)
    await test_db_session.flush()

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.delete(
                f"/api/v1/permissions/{rule.id}", headers=headers
            )
            assert res.status_code == 204

            # Verify rule no longer exists
            deleted_rule = await test_db_session.get(PermissionRule, rule.id)
            assert deleted_rule is None

            # Verify audit log recorded
            stmt = select(AuditLog).where(
                AuditLog.action == "REVOKE_PERMISSION",
                AuditLog.entity_id == table_id,
            )
            audit_result = await test_db_session.execute(stmt)
            audit_entry = audit_result.scalar_one_or_none()
            assert audit_entry is not None
            assert audit_entry.actor_username == data["admin_user"].username
            assert audit_entry.diff_jsonb.get("revoked_rule_id") == str(rule.id)

    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_permissions_api_validation_errors(
    test_db_session: AsyncSession, permissions_api_data: dict
):
    """
    T019: Tests validation error handling:
    - Invalid action -> HTTP 422
    - Invalid resource_type -> HTTP 422
    - Non-existent role_id -> HTTP 404
    - Non-existent rule_id on delete -> HTTP 404
    """
    data = permissions_api_data
    token = data["admin_token"]
    role = data["role"]
    headers = {"Authorization": f"Bearer {token}"}

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Invalid Action
            res1 = await client.post(
                "/api/v1/permissions",
                json={
                    "role_id": str(role.id),
                    "resource_type": "TABLE",
                    "resource_id": str(uuid.uuid4()),
                    "action": "NON_EXISTENT_ACTION",
                    "effect": "ALLOW",
                },
                headers=headers,
            )
            assert res1.status_code == 422

            # 2. Invalid Resource Type
            res2 = await client.post(
                "/api/v1/permissions",
                json={
                    "role_id": str(role.id),
                    "resource_type": "INVALID_RESOURCE",
                    "resource_id": str(uuid.uuid4()),
                    "action": "VIEW_METADATA",
                    "effect": "ALLOW",
                },
                headers=headers,
            )
            assert res2.status_code == 422

            # 3. Non-existent role_id -> 404
            res3 = await client.post(
                "/api/v1/permissions",
                json={
                    "role_id": str(uuid.uuid4()),
                    "resource_type": "TABLE",
                    "resource_id": str(uuid.uuid4()),
                    "action": "VIEW_METADATA",
                    "effect": "ALLOW",
                },
                headers=headers,
            )
            assert res3.status_code == 404

            # 4. Non-existent rule delete -> 404
            res4 = await client.delete(
                f"/api/v1/permissions/{uuid.uuid4()}", headers=headers
            )
            assert res4.status_code == 404

    finally:
        app.dependency_overrides.clear()

