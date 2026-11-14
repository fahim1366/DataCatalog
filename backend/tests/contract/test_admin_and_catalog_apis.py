from __future__ import annotations

import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.database import get_db_session
from backend.src.main import app
from backend.src.models.identity import Role, User, UserStatus
from backend.src.models.catalog import DatabaseSchema, CatalogTable, TableType, ClassificationLevel, CatalogColumn
from backend.src.models.connections import DatabaseConnection, DatabaseEngine
from backend.src.services.auth.jwt_service import auth_service


@pytest.fixture
async def admin_auth_data(test_db_session: AsyncSession):
    """Creates a persistent SuperAdmin user and returns credentials and Bearer token."""
    unique_suffix = uuid.uuid4().hex[:8]

    # Create Connection, Schema, Table, Column
    conn = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"test_conn_{unique_suffix}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host="localhost",
        port=5432,
        database_name=f"db_{unique_suffix}",
        owner="admin@company.internal",
        encrypted_dek=b"k" * 32,
        encrypted_credentials=b"c" * 64,
        is_active=True,
    )
    test_db_session.add(conn)
    await test_db_session.flush()

    schema = DatabaseSchema(
        id=uuid.uuid4(),
        connection_id=conn.id,
        name=f"public_{unique_suffix}",
        description="Public test schema",
    )
    test_db_session.add(schema)
    await test_db_session.flush()

    table = CatalogTable(
        id=uuid.uuid4(),
        schema_id=schema.id,
        name=f"orders_{unique_suffix}",
        table_type=TableType.TABLE,
        row_count_estimate=1000,
        size_bytes=4096,
        owner="admin",
    )
    test_db_session.add(table)
    await test_db_session.flush()

    col = CatalogColumn(
        id=uuid.uuid4(),
        table_id=table.id,
        name="order_id",
        ordinal_position=1,
        data_type="BIGINT",
        raw_data_type="bigint",
        is_primary_key=True,
        classification_level=ClassificationLevel.INTERNAL,
    )
    test_db_session.add(col)
    await test_db_session.flush()

    user = User(
        id=uuid.uuid4(),
        username=f"admin_{unique_suffix}",
        email=f"admin_{unique_suffix}@company.internal",
        full_name="Test Super Admin",
        hashed_password=auth_service.hash_password("Admin#2026"),
        status=UserStatus.ACTIVE,
        is_superadmin=True,
    )
    test_db_session.add(user)
    await test_db_session.flush()

    token = auth_service.create_access_token(
        data={
            "sub": str(user.id),
            "username": user.username,
            "email": user.email,
            "roles": ["SuperAdmin"],
            "is_superadmin": True,
        }
    )

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db

    yield {
        "user": user,
        "token": token,
        "conn": conn,
        "schema": schema,
        "table": table,
        "column": col,
    }

    app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_search_with_empty_query_returns_authorized_tables(
    test_db_session: AsyncSession,
    admin_auth_data: dict,
):
    token = admin_auth_data["token"]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # q="" should return 200 and list of items, not 422
        resp = await client.get(
            "/api/v1/search?q=",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "page_info" in data
        assert len(data["items"]) >= 1


@pytest.mark.asyncio
async def test_login_returns_effective_permissions(
    test_db_session: AsyncSession,
    admin_auth_data: dict,
):
    admin_user = admin_auth_data["user"]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": admin_user.username, "password": "Admin#2026"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "user" in data
        user_ctx = data["user"]
        assert "permissions" in user_ctx
        assert "VIEW_METADATA" in user_ctx["permissions"]
        assert "EDIT_DESCRIPTION" in user_ctx["permissions"]
        assert "MANAGE_TAGS" in user_ctx["permissions"]
        assert "MANAGE_CONNECTIONS" in user_ctx["permissions"]


@pytest.mark.asyncio
async def test_physical_and_category_trees(
    test_db_session: AsyncSession,
    admin_auth_data: dict,
):
    token = admin_auth_data["token"]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Physical tree
        resp_phys = await client.get(
            "/api/v1/tables/tree/physical",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp_phys.status_code == 200
        phys_data = resp_phys.json()
        assert isinstance(phys_data, list)
        assert len(phys_data) >= 1

        # Category tree
        resp_cat = await client.get(
            "/api/v1/categories/tree",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp_cat.status_code == 200
        cat_data = resp_cat.json()
        assert isinstance(cat_data, list)


@pytest.mark.asyncio
async def test_users_and_roles_crud(
    test_db_session: AsyncSession,
    admin_auth_data: dict,
):
    token = admin_auth_data["token"]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # List roles
        resp = await client.get(
            "/api/v1/roles",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        roles = resp.json()
        assert isinstance(roles, list)

        # List users
        resp_users = await client.get(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp_users.status_code == 200
        users = resp_users.json()
        assert isinstance(users, list)
        assert len(users) >= 1

        # Test column classification update
        col_id = admin_auth_data["column"].id
        resp_class = await client.patch(
            f"/api/v1/columns/{col_id}/classification",
            json={"classification_level": "CONFIDENTIAL"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp_class.status_code == 200
        assert resp_class.json()["classification_level"] == "CONFIDENTIAL"
