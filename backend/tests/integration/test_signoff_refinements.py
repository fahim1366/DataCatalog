from __future__ import annotations

import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.database import get_db_session
from backend.src.main import app
from backend.src.models.catalog import (
    CatalogColumn,
    CatalogIndex,
    CatalogRoutine,
    CatalogTable,
    DatabaseSchema,
    LifecycleStatus,
    TableType,
)
from backend.src.models.connections import DatabaseConnection, DatabaseEngine
from backend.src.models.identity import User, UserStatus
from backend.src.schemas.ingestion import (
    BulkIngestionRequest,
    IngestColumnPayload,
    IngestIndexPayload,
    IngestRoutinePayload,
    IngestTablePayload,
)
from backend.src.services.auth.jwt_service import auth_service
from backend.src.services.ingestion.bulk_ingestion_service import bulk_ingestion_service


@pytest.fixture
async def admin_auth_data(test_db_session: AsyncSession):
    suffix = uuid.uuid4().hex[:8]
    admin_user = User(
        id=uuid.uuid4(),
        username=f"superadmin_{suffix}",
        email=f"superadmin_{suffix}@company.internal",
        hashed_password=auth_service.hash_password("SuperSecret#2026"),
        full_name="Super Admin",
        status=UserStatus.ACTIVE,
        is_superadmin=True,
    )
    test_db_session.add(admin_user)
    await test_db_session.commit()
    await test_db_session.refresh(admin_user)

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db

    token = auth_service.create_access_token(
        data={
            "sub": str(admin_user.id),
            "username": admin_user.username,
            "email": admin_user.email,
            "is_superadmin": True,
            "roles": ["SUPERADMIN"],
        }
    )

    yield {
        "user": admin_user,
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"},
    }

    app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_ssrf_protection_on_discover_and_test_endpoints(admin_auth_data):
    """
    Asserts SSRF Protection rejects link-local / cloud metadata IP ranges (169.254.0.0/16, 0.0.0.0/8)
    and empty/whitespace hostnames with HTTP 400 Bad Request and the Persian error message.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        blocked_hosts = [
            "169.254.169.254",
            "169.254.1.1",
            "0.0.0.0",
            "0.1.2.3",
            "",
            "   ",
        ]

        # Test POST /api/v1/connections/discover-databases
        for host in blocked_hosts:
            payload = {
                "engine_type": "POSTGRESQL",
                "host": host,
                "port": 5432,
                "credentials": {"username": "postgres", "password": "pw"},
                "custom_params": {},
            }
            res = await client.post(
                "/api/v1/connections/discover-databases",
                json=payload,
                headers=admin_auth_data["headers"],
            )
            assert res.status_code == 400, f"Expected 400 for host '{host}', got {res.status_code}"
            assert res.json().get("detail") == "آدرس سرور وارد شده مجاز نیست."

        # Test POST /api/v1/connections/test-unsaved
        for host in blocked_hosts:
            payload = {
                "engine_type": "POSTGRESQL",
                "host": host,
                "port": 5432,
                "database_name": "postgres",
                "credentials": {"username": "postgres", "password": "pw"},
                "custom_params": {},
            }
            res = await client.post(
                "/api/v1/connections/test-unsaved",
                json=payload,
                headers=admin_auth_data["headers"],
            )
            assert res.status_code == 400, f"Expected 400 for host '{host}', got {res.status_code}"
            assert res.json().get("detail") == "آدرس سرور وارد شده مجاز نیست."


@pytest.mark.asyncio
async def test_bulk_ingestion_refreshes_indexes_and_routines_cleanly(test_db_session: AsyncSession, admin_auth_data):
    """
    Asserts bulk_ingestion_service replaces/refreshes schema routines and table indexes cleanly on re-sync
    so dropped indexes or routines do not linger as stale records.
    """
    suffix = uuid.uuid4().hex[:8]
    conn = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"test_conn_{suffix}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host="localhost",
        port=5432,
        database_name=f"test_db_{suffix}",
        owner=admin_auth_data["user"].username,
        kek_version=1,
        encrypted_dek=b"dummy_dek_32_bytes_bulk_ingest",
        encrypted_credentials=b"dummy_credentials_bulk_ingest",
    )
    test_db_session.add(conn)
    await test_db_session.flush()

    schema_name = f"sync_schema_{suffix}"

    # Batch 1: Ingest 1 table with 2 indexes, and 2 routines
    batch_1 = BulkIngestionRequest(
        connection_id=conn.id,
        schema_name=schema_name,
        tables=[
            IngestTablePayload(
                name="users",
                table_type=TableType.TABLE,
                columns=[
                    IngestColumnPayload(name="id", ordinal_position=1, data_type="integer", raw_data_type="int4"),
                    IngestColumnPayload(name="email", ordinal_position=2, data_type="varchar", raw_data_type="varchar(255)"),
                    IngestColumnPayload(name="username", ordinal_position=3, data_type="varchar", raw_data_type="varchar(100)"),
                ],
            )
        ],
        indexes=[
            IngestIndexPayload(table_name="users", name="idx_users_email", is_unique=True, columns_list=["email"]),
            IngestIndexPayload(table_name="users", name="idx_users_username_to_drop", is_unique=False, columns_list=["username"]),
        ],
        routines=[
            IngestRoutinePayload(name="fn_get_user_email", routine_type="FUNCTION", language="sql", return_type="varchar"),
            IngestRoutinePayload(name="sp_archive_user_to_drop", routine_type="PROCEDURE", language="plpgsql", return_type="void"),
        ],
    )

    resp_1 = await bulk_ingestion_service.ingest_bulk_metadata(
        session=test_db_session,
        payload=batch_1,
        actor_id=admin_auth_data["user"].id,
        actor_username=admin_auth_data["user"].username,
    )
    assert resp_1.success is True

    # Check that both indexes and both routines exist
    schema_id = resp_1.schema_id
    idx_res = await test_db_session.execute(
        select(CatalogIndex).where(CatalogIndex.schema_id == schema_id)
    )
    indexes_1 = idx_res.scalars().all()
    assert len(indexes_1) == 2
    idx_names_1 = {idx.name for idx in indexes_1}
    assert idx_names_1 == {"idx_users_email", "idx_users_username_to_drop"}

    rt_res = await test_db_session.execute(
        select(CatalogRoutine).where(CatalogRoutine.schema_id == schema_id)
    )
    routines_1 = rt_res.scalars().all()
    assert len(routines_1) == 2
    rt_names_1 = {rt.name for rt in routines_1}
    assert rt_names_1 == {"fn_get_user_email", "sp_archive_user_to_drop"}

    # Batch 2: Re-sync where 1 index and 1 routine were dropped in the database
    batch_2 = BulkIngestionRequest(
        connection_id=conn.id,
        schema_name=schema_name,
        tables=[
            IngestTablePayload(
                name="users",
                table_type=TableType.TABLE,
                columns=[
                    IngestColumnPayload(name="id", ordinal_position=1, data_type="integer", raw_data_type="int4"),
                    IngestColumnPayload(name="email", ordinal_position=2, data_type="varchar", raw_data_type="varchar(255)"),
                ],
            )
        ],
        indexes=[
            # idx_users_username_to_drop is dropped! Only idx_users_email remains
            IngestIndexPayload(table_name="users", name="idx_users_email", is_unique=True, columns_list=["email"]),
        ],
        routines=[
            # sp_archive_user_to_drop is dropped! Only fn_get_user_email remains
            IngestRoutinePayload(name="fn_get_user_email", routine_type="FUNCTION", language="sql", return_type="varchar"),
        ],
    )

    resp_2 = await bulk_ingestion_service.ingest_bulk_metadata(
        session=test_db_session,
        payload=batch_2,
        actor_id=admin_auth_data["user"].id,
        actor_username=admin_auth_data["user"].username,
    )
    assert resp_2.success is True

    # Verify dropped index and routine are deleted and do not linger
    idx_res_2 = await test_db_session.execute(
        select(CatalogIndex).where(CatalogIndex.schema_id == schema_id)
    )
    indexes_2 = idx_res_2.scalars().all()
    assert len(indexes_2) == 1
    assert indexes_2[0].name == "idx_users_email"

    rt_res_2 = await test_db_session.execute(
        select(CatalogRoutine).where(CatalogRoutine.schema_id == schema_id)
    )
    routines_2 = rt_res_2.scalars().all()
    assert len(routines_2) == 1
    assert routines_2[0].name == "fn_get_user_email"
