import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.src.main import app
from backend.src.models.identity import Role, User, UserRole, UserStatus
from backend.src.models.connections import DatabaseConnection, DatabaseEngine, SyncSchedule
from backend.src.models.catalog import DatabaseSchema, CatalogTable, CatalogColumn, TableType, LifecycleStatus
from backend.src.models.rbac import Category, CategoryAssignment, EntityType, PermissionAction, PermissionEffect, PermissionRule, ResourceType
from backend.src.services.auth.jwt_service import auth_service


@pytest.mark.asyncio
async def test_admin_governance_leaf_category_and_role_deletion(test_db_session: AsyncSession):
    # 1. Setup superadmin
    admin = User(
        id=uuid.uuid4(),
        username=f"admin_{uuid.uuid4().hex[:6]}",
        email=f"admin_{uuid.uuid4().hex[:6]}@test.com",
        full_name="Admin Test",
        hashed_password=auth_service.hash_password("Password123!"),
        status=UserStatus.ACTIVE,
        is_superadmin=True,
    )
    test_db_session.add(admin)
    await test_db_session.commit()

    token = auth_service.create_access_token({"sub": str(admin.id), "username": admin.username})
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 2. Test Category Deletion Conflict when assigned to Database
        cat = Category(
            id=uuid.uuid4(),
            name="موضوع تستی پایگاه داده",
            status="ACTIVE",
        )
        test_db_session.add(cat)
        conn = DatabaseConnection(
            id=uuid.uuid4(),
            name=f"TestDB_{uuid.uuid4().hex[:6]}",
            engine_type=DatabaseEngine.MONGODB,
            host="localhost",
            port=27017,
            database_name="mongo_db",
            owner="admin",
            kek_version=1,
            encrypted_dek=b"dek" * 10,
            encrypted_credentials=b"creds" * 10,
        )
        test_db_session.add(conn)
        await test_db_session.flush()

        cat_assign = CategoryAssignment(
            id=uuid.uuid4(),
            category_id=cat.id,
            entity_type=EntityType.DATABASE,
            entity_id=conn.id,
        )
        test_db_session.add(cat_assign)
        await test_db_session.commit()

        # Attempt to delete category with assigned database -> HTTP 409
        del_cat_resp = await client.delete(f"/api/v1/categories/{cat.id}", headers=headers)
        assert del_cat_resp.status_code == 409
        assert "امکان‌پذیر نیست: به این دسته‌بندی پایگاه داده اختصاص داده شده است." in del_cat_resp.json()["detail"]

        # 3. Test Role Deletion Conflict when assigned to users -> HTTP 409
        role = Role(
            id=uuid.uuid4(),
            name=f"AssignedRole_{uuid.uuid4().hex[:6]}",
            description="Role with assigned user",
            is_system_role=False,
        )
        test_db_session.add(role)
        await test_db_session.flush()

        user_role = UserRole(
            user_id=admin.id,
            role_id=role.id,
            assigned_by=admin.id,
        )
        test_db_session.add(user_role)
        await test_db_session.commit()

        del_role_resp = await client.delete(f"/api/v1/roles/{role.id}", headers=headers)
        assert del_role_resp.status_code == 409
        assert "امکان حذف وجود ندارد: این نقش به یک یا چند کاربر اختصاص یافته است." in del_role_resp.json()["detail"]

        # 4. Test Schedule Endpoints (Get, Update, Trigger Sync)
        sched_get = await client.get(f"/api/v1/connections/{conn.id}/schedule", headers=headers)
        assert sched_get.status_code == 200
        assert sched_get.json()["connection_id"] == str(conn.id)

        sched_update = await client.put(
            f"/api/v1/connections/{conn.id}/schedule",
            headers=headers,
            json={"cron_expression": "*/15 * * * *", "is_enabled": True},
        )
        assert sched_update.status_code == 200
        assert sched_update.json()["cron_expression"] == "*/15 * * * *"

        sync_trigger = await client.post(f"/api/v1/connections/{conn.id}/sync", headers=headers)
        assert sync_trigger.status_code == 200
        assert sync_trigger.json()["last_run_status"] == "SUCCESS"


@pytest.mark.asyncio
async def test_ai_description_approval_and_missing_filter(test_db_session: AsyncSession):
    # Setup user & db objects
    admin = User(
        id=uuid.uuid4(),
        username=f"admin_{uuid.uuid4().hex[:6]}",
        email=f"admin_{uuid.uuid4().hex[:6]}@test.com",
        full_name="Admin Test",
        hashed_password=auth_service.hash_password("Password123!"),
        status=UserStatus.ACTIVE,
        is_superadmin=True,
    )
    test_db_session.add(admin)

    conn = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"ApproveDB_{uuid.uuid4().hex[:6]}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host="localhost",
        port=5432,
        database_name="approve_db",
        owner="admin",
        kek_version=1,
        encrypted_dek=b"dek" * 10,
        encrypted_credentials=b"creds" * 10,
    )
    test_db_session.add(conn)
    await test_db_session.flush()

    schema = DatabaseSchema(id=uuid.uuid4(), connection_id=conn.id, name="public")
    test_db_session.add(schema)
    await test_db_session.flush()

    table = CatalogTable(
        id=uuid.uuid4(),
        schema_id=schema.id,
        name="customers",
        table_type=TableType.TABLE,
        status=LifecycleStatus.ACTIVE,
        ai_suggested_description="جدول مشتریان سازمان با پروفایل اعتباری",
    )
    test_db_session.add(table)
    await test_db_session.flush()

    column = CatalogColumn(
        id=uuid.uuid4(),
        table_id=table.id,
        name="national_code",
        ordinal_position=1,
        data_type="varchar",
        raw_data_type="varchar(10)",
        ai_suggested_description="کد ملی ۱۰ رقمی مشتری",
    )
    test_db_session.add(column)
    await test_db_session.commit()

    token = auth_service.create_access_token({"sub": str(admin.id), "username": admin.username})
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Approve table AI description
        tbl_resp = await client.post(f"/api/v1/tables/{table.id}/approve-ai-description", headers=headers)
        assert tbl_resp.status_code == 200
        tbl_data = tbl_resp.json()
        assert tbl_data["approved_description"] == "جدول مشتریان سازمان با پروفایل اعتباری"
        assert tbl_data["description_source"] == "AI_APPROVED"
        assert tbl_data["curated_by_username"] == admin.username

        # Approve column AI description
        col_resp = await client.post(f"/api/v1/columns/{column.id}/approve-ai-description", headers=headers)
        assert col_resp.status_code == 200
        col_data = col_resp.json()
        assert col_data["approved_description"] == "کد ملی ۱۰ رقمی مشتری"
        assert col_data["description_source"] == "AI_APPROVED"
        assert col_data["curated_by_username"] == admin.username

        # Missing descriptions filter test
        missing_resp = await client.get("/api/v1/tables?missing_descriptions_only=true", headers=headers)
        assert missing_resp.status_code == 200
        items = missing_resp.json()["items"]
        assert all(t["id"] != str(table.id) for t in items)
