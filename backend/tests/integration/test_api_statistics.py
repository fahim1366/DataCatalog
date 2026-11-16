from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.database import get_db_session
from backend.src.main import app
from backend.src.models.catalog import CatalogTable, DatabaseSchema, LifecycleStatus, TableType
from backend.src.models.connections import DatabaseConnection, DatabaseEngine
from backend.src.models.identity import User, UserStatus
from backend.src.models.statistics import TableStatisticsHistory
from backend.src.services.auth.jwt_service import auth_service
from backend.src.services.crypto.envelope import EnvelopeEncryptionService


@pytest.fixture
async def admin_auth_data(test_db_session: AsyncSession):
    suffix = uuid.uuid4().hex[:8]
    admin_user = User(
        id=uuid.uuid4(),
        username=f"statsadmin_{suffix}",
        email=f"statsadmin_{suffix}@company.internal",
        hashed_password=auth_service.hash_password("SuperSecret#2026"),
        full_name="Stats Admin",
        status=UserStatus.ACTIVE,
        is_superadmin=True,
    )
    test_db_session.add(admin_user)
    await test_db_session.flush()

    token = auth_service.create_access_token(
        data={
            "sub": str(admin_user.id),
            "username": admin_user.username,
            "email": admin_user.email,
            "roles": ["SuperAdmin"],
            "is_superadmin": True,
        }
    )
    return {
        "user": admin_user,
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"},
        "suffix": suffix,
    }


@pytest.fixture
async def sample_stats_table(test_db_session: AsyncSession, admin_auth_data: dict):
    crypto = EnvelopeEncryptionService()
    enc_dek, enc_creds = crypto.encrypt_credentials({"username": "postgres", "password": "password"})

    conn = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"Stats_Conn_{admin_auth_data['suffix']}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host="127.0.0.1",
        port=5432,
        database_name="enterprise_datacatalog",
        default_schema="public",
        owner=admin_auth_data["user"].username,
        encrypted_dek=enc_dek,
        encrypted_credentials=enc_creds,
        is_active=True,
    )
    test_db_session.add(conn)

    schema = DatabaseSchema(
        id=uuid.uuid4(),
        connection_id=conn.id,
        name=f"stats_schema_{admin_auth_data['suffix']}",
    )
    test_db_session.add(schema)

    table = CatalogTable(
        id=uuid.uuid4(),
        schema_id=schema.id,
        name="sales_records",
        table_type=TableType.TABLE,
        status=LifecycleStatus.ACTIVE,
    )
    test_db_session.add(table)

    # Insert 3 historical statistic points
    now = datetime.now(timezone.utc)
    point1 = TableStatisticsHistory(
        id=uuid.uuid4(),
        table_id=table.id,
        recorded_at=now - timedelta(days=2),
        row_count_estimate=100000,
        size_bytes=10485760,
    )
    point2 = TableStatisticsHistory(
        id=uuid.uuid4(),
        table_id=table.id,
        recorded_at=now - timedelta(days=1),
        row_count_estimate=150000,
        size_bytes=15728640,
    )
    point3 = TableStatisticsHistory(
        id=uuid.uuid4(),
        table_id=table.id,
        recorded_at=now,
        row_count_estimate=200000,
        size_bytes=20971520,
    )
    test_db_session.add_all([point1, point2, point3])
    await test_db_session.commit()
    await test_db_session.refresh(table)
    return table, [point1, point2, point3]


@pytest.mark.asyncio
async def test_get_table_statistics_history(
    test_db_session: AsyncSession, admin_auth_data: dict, sample_stats_table: tuple
):
    table, points = sample_stats_table

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/tables/{table.id}/statistics?limit=30",
                headers=admin_auth_data["headers"],
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["table_id"] == str(table.id)
            assert "data_points" in data
            assert len(data["data_points"]) == 3

            # Verify descending order by recorded_at
            assert data["data_points"][0]["row_count_estimate"] == 200000
            assert data["data_points"][1]["row_count_estimate"] == 150000
            assert data["data_points"][2]["row_count_estimate"] == 100000

            # 404 for non-existent table
            resp_404 = await client.get(
                f"/api/v1/tables/{uuid.uuid4()}/statistics",
                headers=admin_auth_data["headers"],
            )
            assert resp_404.status_code == 404
    finally:
        app.dependency_overrides.pop(get_db_session, None)
