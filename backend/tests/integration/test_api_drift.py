from __future__ import annotations

from datetime import datetime, timezone
import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.database import get_db_session
from backend.src.main import app
from backend.src.models.catalog import CatalogColumn, CatalogTable, DatabaseSchema, LifecycleStatus, TableType
from backend.src.models.connections import DatabaseConnection, DatabaseEngine
from backend.src.models.identity import User, UserStatus
from backend.src.services.auth.jwt_service import auth_service
from backend.src.services.crypto.envelope import EnvelopeEncryptionService


@pytest.fixture
async def admin_auth_data(test_db_session: AsyncSession):
    suffix = uuid.uuid4().hex[:8]
    admin_user = User(
        id=uuid.uuid4(),
        username=f"driftadmin_{suffix}",
        email=f"driftadmin_{suffix}@company.internal",
        hashed_password=auth_service.hash_password("SuperSecret#2026"),
        full_name="Drift Admin",
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
async def sample_drift_table(test_db_session: AsyncSession, admin_auth_data: dict):
    crypto = EnvelopeEncryptionService()
    enc_dek, enc_creds = crypto.encrypt_credentials({"username": "postgres", "password": "password"})

    conn = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"Drift_Conn_{admin_auth_data['suffix']}",
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
        name=f"drift_schema_{admin_auth_data['suffix']}",
    )
    test_db_session.add(schema)

    table = CatalogTable(
        id=uuid.uuid4(),
        schema_id=schema.id,
        name="drifted_orders",
        table_type=TableType.TABLE,
        source_description="Extracted comment from DB catalog",
        approved_description="Human curated description in catalog",
        description_drift_detected=True,
        status=LifecycleStatus.ACTIVE,
    )
    test_db_session.add(table)

    col1 = CatalogColumn(
        id=uuid.uuid4(),
        table_id=table.id,
        name="order_amount",
        ordinal_position=1,
        data_type="numeric(10,2)",
        raw_data_type="numeric",
        source_description="DB amount comment",
        approved_description="Curated amount description",
        description_drift_detected=True,
    )
    col2 = CatalogColumn(
        id=uuid.uuid4(),
        table_id=table.id,
        name="order_id",
        ordinal_position=2,
        data_type="uuid",
        raw_data_type="uuid",
        source_description="Order ID",
        approved_description="Order ID",
        description_drift_detected=False,
    )
    test_db_session.add(col1)
    test_db_session.add(col2)
    await test_db_session.commit()
    await test_db_session.refresh(table)
    return table, col1, col2


@pytest.mark.asyncio
async def test_get_table_drift_details(
    test_db_session: AsyncSession, admin_auth_data: dict, sample_drift_table: tuple
):
    table, col1, col2 = sample_drift_table

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/tables/{table.id}/drift",
                headers=admin_auth_data["headers"],
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["table_id"] == str(table.id)
            assert data["table_name"] == "drifted_orders"
            assert data["drift_detected"] is True
            assert data["table_source_description"] == "Extracted comment from DB catalog"
            assert data["table_approved_description"] == "Human curated description in catalog"
            assert len(data["column_drifts"]) == 2

            col_map = {c["column_name"]: c for c in data["column_drifts"]}
            assert col_map["order_amount"]["drift_detected"] is True
            assert col_map["order_amount"]["source_description"] == "DB amount comment"
            assert col_map["order_id"]["drift_detected"] is False

            # 404 for unknown table
            resp_404 = await client.get(
                f"/api/v1/tables/{uuid.uuid4()}/drift",
                headers=admin_auth_data["headers"],
            )
            assert resp_404.status_code == 404
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_resolve_table_drift_endpoints(
    test_db_session: AsyncSession, admin_auth_data: dict, sample_drift_table: tuple
):
    table, col1, col2 = sample_drift_table

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Resolve table drift with CUSTOM_MERGE and column drift with RETAIN_APPROVED
            payload = {
                "resolution_action": "CUSTOM_MERGE",
                "custom_description": "Merged table description approved by data steward",
                "column_resolutions": [
                    {
                        "column_id": str(col1.id),
                        "resolution_action": "RETAIN_APPROVED",
                    }
                ],
            }
            resp = await client.post(
                f"/api/v1/tables/{table.id}/drift/resolve",
                json=payload,
                headers=admin_auth_data["headers"],
            )
            assert resp.status_code == 200, resp.text
            res_data = resp.json()
            assert res_data["success"] is True
            assert "resolved_at" in res_data

            # Verify in DB
            await test_db_session.refresh(table)
            await test_db_session.refresh(col1)
            assert table.description_drift_detected is False
            assert table.approved_description == "Merged table description approved by data steward"
            assert col1.description_drift_detected is False
            assert col1.approved_description == "Curated amount description"

            # 404 for non-existent table
            resp_404 = await client.post(
                f"/api/v1/tables/{uuid.uuid4()}/drift/resolve",
                json={"resolution_action": "RETAIN_APPROVED"},
                headers=admin_auth_data["headers"],
            )
            assert resp_404.status_code == 404
    finally:
        app.dependency_overrides.pop(get_db_session, None)
