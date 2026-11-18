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


@pytest.mark.asyncio
async def test_export_reports_formats(
    test_db_session: AsyncSession,
    report_api_auth_data: dict,
):
    """
    Test GET /api/v1/reports/export with json, csv (UTF-8 BOM), and xlsx (openpyxl multi-sheet RTL).
    """
    import io
    import openpyxl

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. JSON Export
            json_resp = await client.get(
                "/api/v1/reports/export?format=json",
                headers=report_api_auth_data["exec_headers"],
            )
            assert json_resp.status_code == 200
            json_data = json_resp.json()
            assert "completeness" in json_data
            assert "classification" in json_data
            assert "tables" in json_data
            assert "columns" in json_data

            # 2. CSV Export (must start with UTF-8 BOM)
            csv_resp = await client.get(
                "/api/v1/reports/export?format=csv",
                headers=report_api_auth_data["exec_headers"],
            )
            assert csv_resp.status_code == 200
            assert csv_resp.headers["content-type"].startswith("text/csv")
            assert csv_resp.content.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM
            csv_text = csv_resp.content.decode("utf-8-sig")
            assert "خلاصه مدیریتی" in csv_text
            assert "فرهنگ داده جداول" in csv_text

            # 3. XLSX Export (multi-sheet openpyxl workbook with RTL)
            xlsx_resp = await client.get(
                "/api/v1/reports/export?format=xlsx",
                headers=report_api_auth_data["exec_headers"],
            )
            assert xlsx_resp.status_code == 200
            assert "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" in xlsx_resp.headers["content-type"]
            wb = openpyxl.load_workbook(io.BytesIO(xlsx_resp.content))
            sheet_names = wb.sheetnames
            assert "خلاصه مدیریتی" in sheet_names
            assert "فرهنگ داده جداول" in sheet_names
            assert "فرهنگ داده ستونها" in sheet_names
            for sname in sheet_names:
                ws = wb[sname]
                assert ws.sheet_view.rightToLeft is True
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_export_reports_three_sheet_xlsx_workbook_structure_and_content(
    test_db_session: AsyncSession,
    report_api_auth_data: dict,
):
    """
    Asserts GET /api/v1/reports/export?format=xlsx delivers the enterprise data dictionary:
    - Content-Disposition attachment: filename=enterprise_data_dictionary.xlsx
    - Exactly 3 sheets: 'خلاصه مدیریتی', 'فرهنگ داده جداول', 'فرهنگ داده ستونها'
    - RTL enabled on all three sheets (sheet_view.rightToLeft = True)
    - Full executive KPIs and data dictionary headers rendered with styling
    """
    import io
    import openpyxl

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/reports/export?format=xlsx",
                headers=report_api_auth_data["exec_headers"],
            )
            assert resp.status_code == 200
            assert "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" in resp.headers["content-type"]
            assert "enterprise_data_dictionary.xlsx" in resp.headers.get("content-disposition", "")

            wb = openpyxl.load_workbook(io.BytesIO(resp.content))
            sheet_names = wb.sheetnames
            assert len(sheet_names) == 3, f"Expected exactly 3 sheets, found {len(sheet_names)}: {sheet_names}"
            assert sheet_names == ["خلاصه مدیریتی", "فرهنگ داده جداول", "فرهنگ داده ستونها"]

            # Sheet 1: خلاصه مدیریتی
            ws1 = wb["خلاصه مدیریتی"]
            assert ws1.sheet_view.rightToLeft is True
            assert "گزارش خلاصه حاکمیت داده" in str(ws1.cell(row=1, column=1).value)
            assert ws1.cell(row=3, column=1).value == "شاخص کلیدی حاکمیت داده"
            assert ws1.cell(row=3, column=2).value == "مقدار"

            # Sheet 2: فرهنگ داده جداول
            ws2 = wb["فرهنگ داده جداول"]
            assert ws2.sheet_view.rightToLeft is True
            expected_tbl_headers = [
                "نام پایگاه داده",
                "نام اسکیما",
                "نام جدول",
                "نوع شیء",
                "تعداد رکورد (تخمینی)",
                "حجم (مگابایت)",
                "مالک سازمانی",
                "توضیحات مبدأ (Source)",
                "توضیحات مصوب حاکمیتی (Approved)",
            ]
            for col_idx, expected_h in enumerate(expected_tbl_headers, start=1):
                assert ws2.cell(row=1, column=col_idx).value == expected_h

            # Sheet 3: فرهنگ داده ستونها
            ws3 = wb["فرهنگ داده ستونها"]
            assert ws3.sheet_view.rightToLeft is True
            expected_col_headers = [
                "نام پایگاه داده",
                "نام اسکیما",
                "نام جدول",
                "ترتیب فیلد",
                "نام ستون",
                "نوع داده",
                "نوع کلید",
                "پذیرش تهی (Nullable)",
                "سطح محرمانگی",
                "توضیحات مبدأ (Source)",
                "توضیحات مصوب حاکمیتی (Approved)",
            ]
            for col_idx, expected_h in enumerate(expected_col_headers, start=1):
                assert ws3.cell(row=1, column=col_idx).value == expected_h
    finally:
        app.dependency_overrides.pop(get_db_session, None)

