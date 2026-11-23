from __future__ import annotations

import base64
import json
import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.database import async_session_factory, get_db_session
from backend.src.main import app
from backend.src.models.catalog import (
    CatalogColumn,
    CatalogTable,
    ClassificationLevel,
    DatabaseSchema,
    LifecycleStatus,
    TableType,
)
from backend.src.models.connections import DatabaseConnection, DatabaseEngine
from backend.src.models.identity import Role, User, UserRole, UserStatus
from backend.src.models.rbac import (
    PermissionAction,
    PermissionEffect,
    PermissionRule,
    ResourceType,
)
from backend.src.services.auth.jwt_service import auth_service


@pytest.fixture
async def keyset_test_data(test_db_session: AsyncSession):
    """
    Creates a set of distinct tables with deterministic names to test keyset pagination.
    """
    unique_suffix = uuid.uuid4().hex[:8]

    # Connection & Schema
    connection = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"keyset_conn_{unique_suffix}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host="localhost",
        port=5432,
        database_name=f"keyset_db_{unique_suffix}",
        owner="keyset_owner@company.internal",
        encrypted_dek=b"x" * 32,
        encrypted_credentials=b"y" * 64,
        is_active=True,
    )
    test_db_session.add(connection)
    await test_db_session.flush()

    schema = DatabaseSchema(
        id=uuid.uuid4(),
        connection_id=connection.id,
        name=f"keyset_schema_{unique_suffix}",
        status=LifecycleStatus.ACTIVE,
    )
    test_db_session.add(schema)
    await test_db_session.flush()

    # Create 5 tables with prefix 'search_keyset_test_'
    created_tables = []
    names = ["alpha", "beta", "charlie", "delta", "echo"]
    for name in names:
        tbl = CatalogTable(
            id=uuid.uuid4(),
            schema_id=schema.id,
            name=f"search_keyset_test_{name}_{unique_suffix}",
            table_type=TableType.TABLE,
            row_count_estimate=1000,
            size_bytes=10000,
            source_description=f"Description for keyset test table {name}",
            status=LifecycleStatus.ACTIVE,
        )
        test_db_session.add(tbl)
        created_tables.append(tbl)
    await test_db_session.flush()

    # User & Role with GLOBAL ALLOW
    role = Role(
        id=uuid.uuid4(),
        name=f"KeysetViewer_{unique_suffix}",
        is_system_role=False,
    )
    test_db_session.add(role)
    await test_db_session.flush()

    rule = PermissionRule(
        id=uuid.uuid4(),
        role_id=role.id,
        resource_type=ResourceType.GLOBAL,
        resource_id=None,
        action=PermissionAction.VIEW_METADATA,
        effect=PermissionEffect.ALLOW,
    )
    test_db_session.add(rule)
    await test_db_session.flush()

    user = User(
        id=uuid.uuid4(),
        username=f"keyset_user_{unique_suffix}",
        email=f"keyset_{unique_suffix}@company.internal",
        hashed_password=auth_service.hash_password("Pass#123456"),
        full_name="Keyset Test User",
        status=UserStatus.ACTIVE,
        is_superadmin=False,
    )
    test_db_session.add(user)
    await test_db_session.flush()

    ur = UserRole(user_id=user.id, role_id=role.id)
    test_db_session.add(ur)
    await test_db_session.flush()

    token = auth_service.create_access_token(
        data={
            "sub": str(user.id),
            "username": user.username,
            "email": user.email,
            "roles": [role.name],
            "is_superadmin": False,
        }
    )

    return {
        "connection": connection,
        "schema": schema,
        "tables": created_tables,
        "user": user,
        "role": role,
        "token": token,
        "suffix": unique_suffix,
    }


@pytest.mark.asyncio
async def test_search_unauthenticated_returns_401():
    """Asserts that requests without Authorization header return 401 Unauthorized."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/search?q=test")
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_keyset_pagination_contract(test_db_session: AsyncSession, keyset_test_data: dict):
    """
    Asserts that /api/v1/search conforms to the OpenAPI specification:
    - returns { items: [...], page_info: { has_next_page, next_cursor, limit } }
    - paginates deterministically using next_cursor across consecutive pages without overlap.
    """
    data = keyset_test_data
    token = data["token"]
    suffix = data["suffix"]
    headers = {"Authorization": f"Bearer {token}"}

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Page 1: limit 2
            resp1 = await client.get(
                f"/api/v1/search?q=search_keyset_test_{suffix}&limit=2",
                headers=headers,
            )
            assert resp1.status_code == 200
            body1 = resp1.json()

            assert "items" in body1
            assert "page_info" in body1
            page_info1 = body1["page_info"]
            assert page_info1["limit"] == 2
            assert page_info1["has_next_page"] is True
            assert page_info1["next_cursor"] is not None

            items1 = body1["items"]
            assert len(items1) == 2
            item_ids_page1 = [it["id"] for it in items1]

            # Decode cursor to verify format
            cursor1_str = page_info1["next_cursor"]
            decoded_cursor1 = json.loads(base64.b64decode(cursor1_str.encode("utf-8")).decode("utf-8"))
            assert "val" in decoded_cursor1
            assert "id" in decoded_cursor1

            # Page 2: limit 2, using cursor from page 1
            resp2 = await client.get(
                f"/api/v1/search?q=search_keyset_test_{suffix}&limit=2&cursor={cursor1_str}",
                headers=headers,
            )
            assert resp2.status_code == 200
            body2 = resp2.json()
            items2 = body2["items"]
            assert len(items2) == 2
            item_ids_page2 = [it["id"] for it in items2]

            # Deterministic sorting check: No overlapping IDs between pages
            assert set(item_ids_page1).isdisjoint(set(item_ids_page2))

            # Check alphabetical ordering
            assert items1[0]["name"] <= items1[1]["name"]
            assert items1[1]["name"] <= items2[0]["name"]
            assert items2[0]["name"] <= items2[1]["name"]

            # Page 3: limit 2, using cursor from page 2 (should have remaining 1 table)
            cursor2_str = body2["page_info"]["next_cursor"]
            resp3 = await client.get(
                f"/api/v1/search?q=search_keyset_test_{suffix}&limit=2&cursor={cursor2_str}",
                headers=headers,
            )
            assert resp3.status_code == 200
            body3 = resp3.json()
            items3 = body3["items"]
            assert len(items3) == 1
            assert body3["page_info"]["has_next_page"] is False
            assert body3["page_info"]["next_cursor"] is None
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_schema_tables_and_table_detail_contract(
    test_db_session: AsyncSession, keyset_test_data: dict
):
    """
    Asserts contract adherence for:
    - GET /api/v1/schemas/{schema_id}/tables
    - GET /api/v1/tables/{table_id}
    """
    data = keyset_test_data
    token = data["token"]
    schema_id = data["schema"].id
    first_table = data["tables"][0]
    headers = {"Authorization": f"Bearer {token}"}

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. List schema tables
            resp = await client.get(
                f"/api/v1/schemas/{schema_id}/tables?limit=10",
                headers=headers,
            )
            assert resp.status_code == 200
            body = resp.json()
            assert "items" in body
            assert "page_info" in body
            assert len(body["items"]) == 5

            # 2. Get table detail
            detail_resp = await client.get(
                f"/api/v1/tables/{first_table.id}",
                headers=headers,
            )
            assert detail_resp.status_code == 200
            detail = detail_resp.json()
            assert detail["id"] == str(first_table.id)
            assert detail["name"] == first_table.name
            assert detail["database_name"] == data["connection"].database_name
            assert detail["schema_name"] == data["schema"].name
            assert "columns" in detail
            assert "tags" in detail

            # 3. 404 on non-existent table
            random_id = uuid.uuid4()
            not_found_resp = await client.get(
                f"/api/v1/tables/{random_id}",
                headers=headers,
            )
            assert not_found_resp.status_code == 404
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_search_sort_by_and_min_rows_contract(
    test_db_session: AsyncSession, keyset_test_data: dict
):
    """
    Asserts that /api/v1/search supports:
    - Default sort_by=row_count_desc
    - Explicit sort_by=row_count_asc and sort_by=name_asc
    - min_rows filter
    - Deterministic keyset cursor pagination with row_count_desc
    """
    data = keyset_test_data
    token = data["token"]
    schema = data["schema"]
    headers = {"Authorization": f"Bearer {token}"}
    suffix = uuid.uuid4().hex[:8]

    # Create tables with varying row counts
    counts = [0, 5, 50, 500, 5000]
    tables = []
    for c in counts:
        tbl = CatalogTable(
            id=uuid.uuid4(),
            schema_id=schema.id,
            name=f"sort_test_{c}_{suffix}",
            table_type=TableType.TABLE,
            row_count_estimate=c,
            size_bytes=c * 100,
            status=LifecycleStatus.ACTIVE,
        )
        test_db_session.add(tbl)
        tables.append(tbl)
    await test_db_session.flush()

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Default sort_by=row_count_desc
            resp = await client.get(
                f"/api/v1/search?q=sort_test_&schema={schema.name}",
                headers=headers,
            )
            assert resp.status_code == 200
            items = resp.json()["items"]
            assert len(items) == 5
            row_counts = [it["row_count_estimate"] for it in items]
            assert row_counts == [5000, 500, 50, 5, 0]

            # 2. Keyset pagination with row_count_desc
            page1_resp = await client.get(
                f"/api/v1/search?q=sort_test_&schema={schema.name}&limit=2",
                headers=headers,
            )
            assert page1_resp.status_code == 200
            p1_body = page1_resp.json()
            assert len(p1_body["items"]) == 2
            assert p1_body["items"][0]["row_count_estimate"] == 5000
            assert p1_body["items"][1]["row_count_estimate"] == 500
            assert p1_body["page_info"]["has_next_page"] is True
            cursor1 = p1_body["page_info"]["next_cursor"]
            assert cursor1 is not None

            # Fetch page 2 with cursor
            page2_resp = await client.get(
                f"/api/v1/search?q=sort_test_&schema={schema.name}&limit=2&cursor={cursor1}",
                headers=headers,
            )
            assert page2_resp.status_code == 200
            p2_body = page2_resp.json()
            assert len(p2_body["items"]) == 2
            assert p2_body["items"][0]["row_count_estimate"] == 50
            assert p2_body["items"][1]["row_count_estimate"] == 5

            # 3. sort_by=row_count_asc
            asc_resp = await client.get(
                f"/api/v1/search?q=sort_test_&schema={schema.name}&sort_by=row_count_asc",
                headers=headers,
            )
            assert asc_resp.status_code == 200
            asc_counts = [it["row_count_estimate"] for it in asc_resp.json()["items"]]
            assert asc_counts == [0, 5, 50, 500, 5000]

            # 4. min_rows=10 filter (should exclude 0 and 5)
            min_rows_resp = await client.get(
                f"/api/v1/search?q=sort_test_&schema={schema.name}&min_rows=10",
                headers=headers,
            )
            assert min_rows_resp.status_code == 200
            mr_items = min_rows_resp.json()["items"]
            assert len(mr_items) == 3
            mr_counts = [it["row_count_estimate"] for it in mr_items]
            assert mr_counts == [5000, 500, 50]

            # 5. Invalid sort_by returns 422
            inv_resp = await client.get(
                f"/api/v1/search?sort_by=invalid_sort",
                headers=headers,
            )
            assert inv_resp.status_code == 422
    finally:
        app.dependency_overrides.clear()
