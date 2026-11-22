from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.database import get_db_session
from backend.src.main import app
from backend.src.models.audit import AuditLog
from backend.src.models.connections import DatabaseConnection, DatabaseEngine
from backend.src.models.identity import Role, User, UserRole, UserStatus
from backend.src.models.rbac import (
    EntityType,
    PermissionAction,
    PermissionEffect,
    PermissionRule,
    ResourceType,
)
from backend.src.services.auth.jwt_service import auth_service


@pytest.fixture
async def db_users_test_data(test_db_session: AsyncSession):
    """
    Sets up a SuperAdmin user, a regular user with NO permissions,
    a regular user WITH MANAGE_DB_USERS permission, and a test DatabaseConnection.
    """
    unique_suffix = uuid.uuid4().hex[:8]

    # 1. SuperAdmin User
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

    # 2. Regular User without permission
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

    # 3. DB Operator User with direct MANAGE_DB_USERS permission
    operator_user = User(
        id=uuid.uuid4(),
        username=f"operator_{unique_suffix}",
        email=f"operator_{unique_suffix}@company.internal",
        hashed_password=auth_service.hash_password("Operator#2026"),
        full_name="DB Operator User",
        status=UserStatus.ACTIVE,
        is_superadmin=False,
    )
    test_db_session.add(operator_user)

    # Grant MANAGE_DB_USERS directly to operator_user
    perm_rule = PermissionRule(
        id=uuid.uuid4(),
        user_id=operator_user.id,
        resource_type=ResourceType.GLOBAL,
        resource_id=None,
        action=PermissionAction.MANAGE_DB_USERS,
        effect=PermissionEffect.ALLOW,
    )
    test_db_session.add(perm_rule)

    # 4. Database Connection
    conn = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"test_db_{unique_suffix}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host="127.0.0.1",
        port=5432,
        database_name=f"prod_db_{unique_suffix}",
        owner=admin_user.email,
        encrypted_dek=b"k" * 32,
        encrypted_credentials=b"c" * 64,
        is_active=True,
    )
    test_db_session.add(conn)

    await test_db_session.flush()

    admin_token = auth_service.create_access_token(
        data={"sub": str(admin_user.id), "username": admin_user.username, "roles": ["SuperAdmin"], "is_superadmin": True}
    )
    regular_token = auth_service.create_access_token(
        data={"sub": str(regular_user.id), "username": regular_user.username, "roles": [], "is_superadmin": False}
    )
    operator_token = auth_service.create_access_token(
        data={"sub": str(operator_user.id), "username": operator_user.username, "roles": [], "is_superadmin": False}
    )

    return {
        "admin_user": admin_user,
        "regular_user": regular_user,
        "operator_user": operator_user,
        "connection": conn,
        "admin_token": admin_token,
        "regular_token": regular_token,
        "operator_token": operator_token,
    }


@pytest.mark.asyncio
async def test_permissions_registry_contains_manage_db_users(
    test_db_session: AsyncSession, db_users_test_data: dict
):
    """Verifies that MANAGE_DB_USERS is registered in GET /api/v1/roles/permissions-registry."""
    data = db_users_test_data
    token = data["admin_token"]
    headers = {"Authorization": f"Bearer {token}"}

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/roles/permissions-registry", headers=headers)
            assert resp.status_code == 200
            modules = resp.json()

            found_action = False
            for mod in modules:
                for perm in mod.get("permissions", []):
                    if perm.get("action") == "MANAGE_DB_USERS":
                        found_action = True
                        assert "پایش و مدیریت کاربران" in perm.get("name", "")
                        assert "مشاهده لیست کاربران" in perm.get("description", "")
            assert found_action is True
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_get_db_users_rbac_enforcement(
    test_db_session: AsyncSession, db_users_test_data: dict
):
    """
    Verifies RBAC enforcement on GET /api/v1/connections/{connection_id}/db-users:
    - 401 without auth token
    - 403 for user lacking MANAGE_DB_USERS
    - 200 for user with MANAGE_DB_USERS or SuperAdmin
    """
    data = db_users_test_data
    conn_id = data["connection"].id

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Unauthenticated -> 401
            resp_401 = await client.get(f"/api/v1/connections/{conn_id}/db-users")
            assert resp_401.status_code == 401

            # 2. Regular user without MANAGE_DB_USERS -> 403 Forbidden
            reg_headers = {"Authorization": f"Bearer {data['regular_token']}"}
            resp_403 = await client.get(f"/api/v1/connections/{conn_id}/db-users", headers=reg_headers)
            assert resp_403.status_code == 403
            assert "MANAGE_DB_USERS" in resp_403.json()["detail"]

            # 3. User with MANAGE_DB_USERS permission -> 200 OK
            op_headers = {"Authorization": f"Bearer {data['operator_token']}"}
            mock_users = [
                {
                    "oid": "16384",
                    "username": "app_user",
                    "can_login": True,
                    "is_superuser": False,
                    "can_create_db": False,
                    "can_create_role": False,
                    "connection_limit": 20,
                    "valid_until": "2027-12-31T23:59:59Z",
                    "member_of_roles": ["app_read", "app_write"],
                    "active_sessions": 2,
                }
            ]

            mock_connector = AsyncMock()
            mock_connector.extract_database_users = AsyncMock(return_value=mock_users)
            mock_connector.__aenter__ = AsyncMock(return_value=mock_connector)
            mock_connector.__aexit__ = AsyncMock(return_value=None)

            with patch("backend.src.api.v1.db_users._build_connector_config", return_value={}):
                with patch("backend.src.api.v1.db_users.default_connector_registry.create_connector", return_value=mock_connector):
                    resp_200 = await client.get(f"/api/v1/connections/{conn_id}/db-users", headers=op_headers)
                    assert resp_200.status_code == 200
                    items = resp_200.json()
                    assert len(items) == 1
                    assert items[0]["username"] == "app_user"
                    assert items[0]["can_login"] is True
                    assert items[0]["connection_limit"] == 20
                    assert items[0]["member_of_roles"] == ["app_read", "app_write"]
                    assert items[0]["active_sessions"] == 2
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_update_db_user_rbac_and_audit(
    test_db_session: AsyncSession, db_users_test_data: dict
):
    """
    Verifies PATCH /api/v1/connections/{connection_id}/db-users/{username}:
    - 403 for user lacking MANAGE_DB_USERS
    - 200 for user with MANAGE_DB_USERS
    - Emits immutable AuditLog record (action=UPDATE_DB_USER, entity_type=DATABASE)
    """
    data = db_users_test_data
    conn_id = data["connection"].id
    username = "app_user"

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {
                "can_login": False,
                "connection_limit": 5,
                "valid_until": "2027-06-30",
            }

            # 1. Regular user without permission -> 403
            reg_headers = {"Authorization": f"Bearer {data['regular_token']}"}
            resp_403 = await client.patch(
                f"/api/v1/connections/{conn_id}/db-users/{username}",
                json=payload,
                headers=reg_headers,
            )
            assert resp_403.status_code == 403

            # 2. Operator user with permission -> 200 and AuditLog recorded
            op_headers = {"Authorization": f"Bearer {data['operator_token']}"}
            mock_connector = AsyncMock()
            mock_connector.update_database_user = AsyncMock(
                return_value={"success": True, "message": "کاربر «app_user» با موفقیت به‌روزرسانی شد."}
            )
            mock_connector.__aenter__ = AsyncMock(return_value=mock_connector)
            mock_connector.__aexit__ = AsyncMock(return_value=None)

            with patch("backend.src.api.v1.db_users._build_connector_config", return_value={}):
                with patch("backend.src.api.v1.db_users.default_connector_registry.create_connector", return_value=mock_connector):
                    resp_200 = await client.patch(
                        f"/api/v1/connections/{conn_id}/db-users/{username}",
                        json=payload,
                        headers=op_headers,
                    )
                    assert resp_200.status_code == 200
                    body = resp_200.json()
                    assert body["success"] is True
                    assert "app_user" in body["message"]

            # 3. Verify AuditLog entry was persisted
            audit_stmt = select(AuditLog).where(
                AuditLog.action == "UPDATE_DB_USER",
                AuditLog.entity_id == conn_id,
                AuditLog.entity_type == EntityType.DATABASE,
            )
            audit_res = await test_db_session.execute(audit_stmt)
            audit_log = audit_res.scalar_one_or_none()
            assert audit_log is not None
            assert audit_log.actor_username == data["operator_user"].username
            assert audit_log.diff_jsonb["username"] == username
            assert audit_log.diff_jsonb["changes"]["can_login"] is False
            assert audit_log.diff_jsonb["changes"]["connection_limit"] == 5
            assert audit_log.diff_jsonb["changes"]["valid_until"] == "2027-06-30"
    finally:
        app.dependency_overrides.pop(get_db_session, None)
