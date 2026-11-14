import uuid
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.main import app
from backend.src.core.database import get_db_session
from backend.src.models.identity import Role, User, UserRole, UserStatus
from backend.src.models.rbac import Category, CategoryAssignment, EntityType, PermissionAction, PermissionEffect, PermissionRule, ResourceType
from backend.src.models.connections import DatabaseConnection, DatabaseEngine
from backend.src.models.catalog import CatalogTable, DatabaseSchema, LifecycleStatus, TableType
from backend.src.services.auth.jwt_service import auth_service


@pytest_asyncio.fixture(scope="function")
async def admin_auth_data(test_db_session: AsyncSession):
    """Creates a persistent SuperAdmin user and returns credentials and Bearer token."""
    unique_suffix = uuid.uuid4().hex[:8]

    user = User(
        id=uuid.uuid4(),
        username=f"admin_{unique_suffix}",
        email=f"admin_{unique_suffix}@company.internal",
        full_name="Test Super Admin",
        hashed_password=auth_service.hash_password("Admin#2026!"),
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
    try:
        yield {
            "user": user,
            "token": token,
            "headers": {"Authorization": f"Bearer {token}"},
        }
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_category_tree_full_workflow(test_db_session: AsyncSession, admin_auth_data: dict):
    """
    Validates end-to-end category management workflow:
    - Root category creation with parent_id = None
    - Child category creation with exact parent_id
    - Nested category hierarchy (multiple levels)
    - Tree structure representation
    - Category editing
    - Safe deletion protection (subcategories and assigned tables)
    - Table assignments & duplicate prevention
    - Listing category assignments
    - Leaf category deletion
    """
    headers = admin_auth_data["headers"]
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create Root Category (parent_id = None)
        root_payload = {
            "name": "حوزه اصلی مالی و سازمانی",
            "description": "دسته‌بندی ارشد هلدینگ",
            "parent_id": None,
            "status": "ACTIVE",
        }
        res_root = await client.post("/api/v1/categories", json=root_payload, headers=headers)
        assert res_root.status_code == 201
        root_data = res_root.json()
        assert root_data["name"] == root_payload["name"]
        assert root_data["parent_id"] is None
        assert root_data["status"] == "ACTIVE"
        root_id = root_data["id"]

        # 2. Create Child Category (parent_id = root_id)
        child_payload = {
            "name": "مدیریت حسابداری",
            "description": "امور مالیاتی و اسناد دریافتنی",
            "parent_id": root_id,
            "status": "ACTIVE",
        }
        res_child = await client.post("/api/v1/categories", json=child_payload, headers=headers)
        assert res_child.status_code == 201
        child_data = res_child.json()
        assert child_data["parent_id"] == root_id
        child_id = child_data["id"]

        # 3. Create Nested Sub-child (3rd Level)
        subchild_payload = {
            "name": "تراکنش‌های بانکی و پوز",
            "description": "لاگ‌های تراکنش بانکی",
            "parent_id": child_id,
            "status": "ACTIVE",
        }
        res_subchild = await client.post("/api/v1/categories", json=subchild_payload, headers=headers)
        assert res_subchild.status_code == 201
        subchild_id = res_subchild.json()["id"]

        # 4. Verify Tree hierarchy
        res_tree = await client.get("/api/v1/categories/tree", headers=headers)
        assert res_tree.status_code == 200
        tree = res_tree.json()
        root_in_tree = next((n for n in tree if n["id"] == root_id), None)
        assert root_in_tree is not None
        assert root_in_tree["name"] == root_payload["name"]
        assert len(root_in_tree["children"]) >= 1

        child_in_tree = next((c for c in root_in_tree["children"] if c["id"] == child_id), None)
        assert child_in_tree is not None
        assert child_in_tree["parent_id"] == root_id
        assert len(child_in_tree["children"]) >= 1

        subchild_in_tree = next((sc for sc in child_in_tree["children"] if sc["id"] == subchild_id), None)
        assert subchild_in_tree is not None
        assert subchild_in_tree["parent_id"] == child_id

        # 5. Edit Category
        update_payload = {
            "name": "حوزه اصلی مالی و عملیاتی پیشرفته",
            "description": "توضیحات بروزرسانی شده",
            "parent_id": None,
            "status": "ACTIVE",
        }
        res_update = await client.put(f"/api/v1/categories/{root_id}", json=update_payload, headers=headers)
        assert res_update.status_code == 200
        assert res_update.json()["name"] == update_payload["name"]

        # Cannot set self as parent
        res_self_parent = await client.put(f"/api/v1/categories/{root_id}", json={
            "name": "تست",
            "parent_id": root_id,
        }, headers=headers)
        assert res_self_parent.status_code == 400

        # 6. Seed a test database connection, schema, and table for assignment
        conn = DatabaseConnection(
            id=uuid.uuid4(),
            name="Test Finance DB",
            engine_type=DatabaseEngine.POSTGRESQL,
            host="localhost",
            port=5432,
            database_name="finance_db",
            owner="admin@company.internal",
            kek_version=1,
            encrypted_dek=b"dek" * 10,
            encrypted_credentials=b"creds" * 10,
        )
        test_db_session.add(conn)
        await test_db_session.flush()

        schema = DatabaseSchema(
            id=uuid.uuid4(),
            connection_id=conn.id,
            name="treasury",
        )
        test_db_session.add(schema)
        await test_db_session.flush()

        table = CatalogTable(
            id=uuid.uuid4(),
            schema_id=schema.id,
            name="daily_settlements",
            table_type=TableType.TABLE,
            status=LifecycleStatus.ACTIVE,
            row_count_estimate=15000,
        )
        test_db_session.add(table)
        await test_db_session.flush()

        # 7. Assign Table to Category
        assign_payload = {
            "category_id": subchild_id,
            "entity_type": "TABLE",
            "entity_id": str(table.id),
        }
        res_assign = await client.post("/api/v1/categories/assignments", json=assign_payload, headers=headers)
        assert res_assign.status_code == 201

        # Duplicate Assignment Prevention (409 Conflict)
        res_dup = await client.post("/api/v1/categories/assignments", json=assign_payload, headers=headers)
        assert res_dup.status_code == 409

        # Verify assignments list endpoint
        res_list_assign = await client.get(f"/api/v1/categories/assignments?category_id={subchild_id}", headers=headers)
        assert res_list_assign.status_code == 200
        assignments_data = res_list_assign.json()
        assert len(assignments_data) >= 1
        assert assignments_data[0]["table_name"] == "daily_settlements"
        assert assignments_data[0]["schema_name"] == "treasury"
        assert assignments_data[0]["database_name"] == "Test Finance DB"

        # Verify available-tables endpoint
        res_avail = await client.get("/api/v1/categories/available-tables?search=settle", headers=headers)
        assert res_avail.status_code == 200
        avail_tables = res_avail.json()
        assert any(t["id"] == str(table.id) for t in avail_tables)

        # Verify table appears in category tree
        res_tree_after = await client.get("/api/v1/categories/tree", headers=headers)
        tree_after = res_tree_after.json()
        r_node = next(n for n in tree_after if n["id"] == root_id)
        c_node = next(c for c in r_node["children"] if c["id"] == child_id)
        sc_node = next(sc for sc in c_node["children"] if sc["id"] == subchild_id)
        assert sc_node["assigned_tables_count"] == 1
        assert len(sc_node["tables"]) == 1
        assert sc_node["tables"][0]["name"] == "daily_settlements"

        # 8. Safe Deletion Checks:
        # A. Cannot delete root because it has subcategories
        res_del_root = await client.delete(f"/api/v1/categories/{root_id}", headers=headers)
        assert res_del_root.status_code == 400
        assert "زیردسته" in res_del_root.json()["detail"]

        # B. Cannot delete subchild because it has assigned tables
        res_del_subchild = await client.delete(f"/api/v1/categories/{subchild_id}", headers=headers)
        assert res_del_subchild.status_code == 400
        assert "جداول اختصاص‌یافته" in res_del_subchild.json()["detail"]

        # Unassign table
        res_unassign = await client.delete(
            f"/api/v1/categories/assignments/{subchild_id}/TABLE/{table.id}",
            headers=headers,
        )
        assert res_unassign.status_code == 204

        # Now subchild has no assignments, so deletion succeeds
        res_del_subchild_ok = await client.delete(f"/api/v1/categories/{subchild_id}", headers=headers)
        assert res_del_subchild_ok.status_code == 204

        # Delete child
        res_del_child_ok = await client.delete(f"/api/v1/categories/{child_id}", headers=headers)
        assert res_del_child_ok.status_code == 204

        # Delete root
        res_del_root_ok = await client.delete(f"/api/v1/categories/{root_id}", headers=headers)
        assert res_del_root_ok.status_code == 204


@pytest.mark.asyncio
async def test_category_permission_enforcement(test_db_session: AsyncSession):
    """
    Asserts that unauthorized users without MANAGE_TAGS permission
    are blocked with 403 Forbidden from mutating categories.
    """
    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    try:
        # Create an unprivileged user
        viewer_user = User(
            id=uuid.uuid4(),
            username=f"viewer_user_{uuid.uuid4().hex[:6]}",
            email=f"viewer_{uuid.uuid4().hex[:6]}@metasphere.local",
            hashed_password=auth_service.hash_password("Password123!"),
            full_name="Viewer User",
            status=UserStatus.ACTIVE,
            is_superadmin=False,
        )
        test_db_session.add(viewer_user)
        await test_db_session.flush()

        token = auth_service.create_access_token(
            data={"sub": str(viewer_user.id), "username": viewer_user.username, "roles": []}
        )
        headers = {"Authorization": f"Bearer {token}"}
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Viewer can view category tree
            res_tree = await client.get("/api/v1/categories/tree", headers=headers)
            assert res_tree.status_code == 200

            # Viewer CANNOT create category (403)
            res_post = await client.post("/api/v1/categories", json={"name": "تست غیرمجاز"}, headers=headers)
            assert res_post.status_code == 403

            # Viewer CANNOT assign table (403)
            res_assign = await client.post("/api/v1/categories/assignments", json={
                "category_id": str(uuid.uuid4()),
                "entity_type": "TABLE",
                "entity_id": str(uuid.uuid4()),
            }, headers=headers)
            assert res_assign.status_code == 403

            # Viewer CANNOT delete category (403)
            res_del = await client.delete(f"/api/v1/categories/{uuid.uuid4()}", headers=headers)
            assert res_del.status_code == 403
    finally:
        app.dependency_overrides.pop(get_db_session, None)
