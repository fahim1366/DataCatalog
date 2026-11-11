from __future__ import annotations

import time
import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.database import get_db_session
from backend.src.main import app
from backend.src.models.identity import Role, User, UserRole, UserStatus
from backend.src.models.rbac import (
    PermissionAction,
    PermissionEffect,
    PermissionRule,
    ResourceType,
)
from backend.src.services.auth.jwt_service import auth_service


@pytest.fixture
async def report_api_auth_data(test_db_session: AsyncSession):
    """Sets up an admin user, an executive user with EXPORT_REPORTS, and an unprivileged user."""
    suffix = uuid.uuid4().hex[:8]

    # Role 1: Executive Role with EXPORT_REPORTS
    exec_role = Role(
        id=uuid.uuid4(),
        name=f"ExecutiveRole_{suffix}",
        description="Executive role with report viewing permission",
        is_system_role=False,
    )
    # Role 2: Denied Role with explicit DENY on EXPORT_REPORTS
    denied_role = Role(
        id=uuid.uuid4(),
        name=f"DeniedRole_{suffix}",
        description="Role denied from reporting",
        is_system_role=False,
    )
    test_db_session.add_all([exec_role, denied_role])
    await test_db_session.flush()

    # Grant EXPORT_REPORTS to Executive Role
    allow_perm = PermissionRule(
        id=uuid.uuid4(),
        role_id=exec_role.id,
        resource_type=ResourceType.GLOBAL,
        resource_id=None,
        action=PermissionAction.EXPORT_REPORTS,
        effect=PermissionEffect.ALLOW,
    )
    # Explicit DENY for Denied Role
    deny_perm = PermissionRule(
        id=uuid.uuid4(),
        role_id=denied_role.id,
        resource_type=ResourceType.GLOBAL,
        resource_id=None,
        action=PermissionAction.EXPORT_REPORTS,
        effect=PermissionEffect.DENY,
    )
    test_db_session.add_all([allow_perm, deny_perm])
    await test_db_session.flush()

    # User 1: Superadmin
    admin = User(
        id=uuid.uuid4(),
        username=f"admin_rep_{suffix}",
        email=f"admin_rep_{suffix}@example.com",
        hashed_password=auth_service.hash_password("Pass1234#"),
        full_name="Report Admin",
        status=UserStatus.ACTIVE,
        is_superadmin=True,
    )
    # User 2: Executive User
    exec_user = User(
        id=uuid.uuid4(),
        username=f"exec_user_{suffix}",
        email=f"exec_{suffix}@example.com",
        hashed_password=auth_service.hash_password("Pass1234#"),
        full_name="Executive Viewer",
        status=UserStatus.ACTIVE,
        is_superadmin=False,
    )
    # User 3: Denied User
    denied_user = User(
        id=uuid.uuid4(),
        username=f"denied_user_{suffix}",
        email=f"denied_{suffix}@example.com",
        hashed_password=auth_service.hash_password("Pass1234#"),
        full_name="Denied Viewer",
        status=UserStatus.ACTIVE,
        is_superadmin=False,
    )
    test_db_session.add_all([admin, exec_user, denied_user])
    await test_db_session.flush()

    # Assign roles
    test_db_session.add(UserRole(user_id=exec_user.id, role_id=exec_role.id))
    test_db_session.add(UserRole(user_id=denied_user.id, role_id=denied_role.id))
    await test_db_session.flush()

    admin_token = auth_service.create_access_token(
        data={
            "sub": str(admin.id),
            "username": admin.username,
            "email": admin.email,
            "roles": ["Superadmin"],
            "is_superadmin": True,
        }
    )
    exec_token = auth_service.create_access_token(
        data={
            "sub": str(exec_user.id),
            "username": exec_user.username,
            "email": exec_user.email,
            "roles": [exec_role.name],
            "is_superadmin": False,
        }
    )
    denied_token = auth_service.create_access_token(
        data={
            "sub": str(denied_user.id),
            "username": denied_user.username,
            "email": denied_user.email,
            "roles": [denied_role.name],
            "is_superadmin": False,
        }
    )

    return {
        "admin_headers": {"Authorization": f"Bearer {admin_token}"},
        "exec_headers": {"Authorization": f"Bearer {exec_token}"},
        "denied_headers": {"Authorization": f"Bearer {denied_token}"},
    }


@pytest.mark.asyncio
async def test_unauthenticated_reports_endpoints():
    """Asserts that requests without Authorization header return 401 Unauthorized."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp1 = await client.get("/api/v1/reports/completeness")
        assert resp1.status_code == 401

        resp2 = await client.get("/api/v1/reports/classification-distribution")
        assert resp2.status_code == 401

        resp3 = await client.post("/api/v1/reports/refresh")
        assert resp3.status_code == 401


@pytest.mark.asyncio
async def test_unauthorized_user_reports_access(test_db_session: AsyncSession, report_api_auth_data: dict):
    """Asserts that users with explicit DENY or lacking reporting permission return 403 Forbidden."""
    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/reports/completeness",
                headers=report_api_auth_data["denied_headers"],
            )
            assert resp.status_code == 403
            assert "Forbidden" in resp.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_get_metadata_completeness_contract_and_latency(
    test_db_session: AsyncSession,
    report_api_auth_data: dict,
):
    """
    T041 Step A: Assert GET /api/v1/reports/completeness returns the aggregated JSON payload
    conforming to openapi.yaml CompletenessReport in < 200ms.
    """
    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            start_time = time.perf_counter()
            resp = await client.get(
                "/api/v1/reports/completeness",
                headers=report_api_auth_data["exec_headers"],
            )
            latency_ms = (time.perf_counter() - start_time) * 1000

            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            assert latency_ms < 200.0, f"Response latency {latency_ms:.2f}ms exceeded 200ms SLA"

            body = resp.json()
            assert "overall_completeness_pct" in body
            assert isinstance(body["overall_completeness_pct"], (int, float))
            assert "total_databases" in body
            assert isinstance(body["total_databases"], int)
            assert "total_tables" in body
            assert isinstance(body["total_tables"], int)
            assert "total_columns" in body
            assert isinstance(body["total_columns"], int)
            assert "database_breakdown" in body
            assert isinstance(body["database_breakdown"], list)

            for item in body["database_breakdown"]:
                assert "connection_id" in item
                assert "database_name" in item
                assert "table_count" in item
                assert "tables_with_desc" in item
                assert "tables_with_owner" in item
                assert "completeness_pct" in item
                assert isinstance(item["completeness_pct"], (int, float))
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_get_classification_distribution_contract_and_latency(
    test_db_session: AsyncSession,
    report_api_auth_data: dict,
):
    """
    T041 Step A: Assert GET /api/v1/reports/classification-distribution returns the aggregated
    JSON payload conforming to openapi.yaml ClassificationReport in < 200ms.
    """
    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            start_time = time.perf_counter()
            resp = await client.get(
                "/api/v1/reports/classification-distribution",
                headers=report_api_auth_data["exec_headers"],
            )
            latency_ms = (time.perf_counter() - start_time) * 1000

            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            assert latency_ms < 200.0, f"Response latency {latency_ms:.2f}ms exceeded 200ms SLA"

            body = resp.json()
            assert "total_classified_columns" in body
            assert isinstance(body["total_classified_columns"], int)
            assert "breakdown" in body
            assert isinstance(body["breakdown"], list)

            for item in body["breakdown"]:
                assert "classification_level" in item
                assert "column_count" in item
                assert "affected_table_count" in item
                assert isinstance(item["column_count"], int)
                assert isinstance(item["affected_table_count"], int)
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_post_refresh_materialized_views_contract(
    test_db_session: AsyncSession,
    report_api_auth_data: dict,
):
    """
    T041 Step A: Assert POST /api/v1/reports/refresh triggers concurrent refresh
    and returns 200 SUCCESS.
    """
    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/reports/refresh",
                headers=report_api_auth_data["admin_headers"],
            )
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            body = resp.json()
            assert body["status"] == "SUCCESS"
            assert "refreshed_views" in body
            assert "mv_metadata_completeness" in body["refreshed_views"]
            assert "mv_classification_distribution" in body["refreshed_views"]
    finally:
        app.dependency_overrides.pop(get_db_session, None)
