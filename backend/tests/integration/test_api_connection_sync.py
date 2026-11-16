from __future__ import annotations

import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.config import settings
from backend.src.core.database import get_db_session
from backend.src.main import app
from backend.src.models.connections import DatabaseConnection, DatabaseEngine, SyncSchedule
from backend.src.models.identity import User, UserStatus
from backend.src.models.sync_jobs import SyncJob, SyncJobStatus, SyncTriggerType
from backend.src.services.auth.jwt_service import auth_service
from backend.src.services.crypto.envelope import EnvelopeEncryptionService


@pytest.fixture
async def admin_auth_data(test_db_session: AsyncSession):
    suffix = uuid.uuid4().hex[:8]
    admin_user = User(
        id=uuid.uuid4(),
        username=f"syncadmin_{suffix}",
        email=f"syncadmin_{suffix}@company.internal",
        hashed_password=auth_service.hash_password("SuperSecret#2026"),
        full_name="Sync Admin",
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
async def sample_connection(test_db_session: AsyncSession, admin_auth_data: dict):
    bind_url = test_db_session.bind.url if test_db_session.bind else None
    if bind_url:
        pg_host = bind_url.host or "127.0.0.1"
        pg_port = bind_url.port or 5432
        pg_user = bind_url.username or "postgres"
        pg_pass = bind_url.password or "12345678"
        pg_db = bind_url.database or "enterprise_datacatalog"
    else:
        from sqlalchemy.engine import make_url
        parsed = make_url(settings.DATABASE_URL)
        pg_host = parsed.host or "127.0.0.1"
        pg_port = parsed.port or 5432
        pg_user = parsed.username or "postgres"
        pg_pass = parsed.password or "12345678"
        pg_db = parsed.database or "enterprise_datacatalog"

    crypto = EnvelopeEncryptionService()
    enc_dek, enc_creds = crypto.encrypt_credentials({"username": pg_user, "password": pg_pass})

    conn = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"API_Sync_Conn_{admin_auth_data['suffix']}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host=pg_host,
        port=pg_port,
        database_name=pg_db,
        default_schema="public",
        owner=admin_auth_data["user"].username,
        encrypted_dek=enc_dek,
        encrypted_credentials=enc_creds,
        is_active=True,
    )
    test_db_session.add(conn)

    schedule = SyncSchedule(
        id=uuid.uuid4(),
        connection_id=conn.id,
        cron_expression="0 2 * * *",
        is_enabled=True,
        circuit_state="CLOSED",
        consecutive_failures=0,
    )
    test_db_session.add(schedule)
    await test_db_session.commit()
    await test_db_session.refresh(conn)
    return conn


@pytest.mark.asyncio
async def test_connection_diagnostic_test_endpoint(
    test_db_session: AsyncSession, admin_auth_data: dict, sample_connection: DatabaseConnection
):
    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/connections/{sample_connection.id}/test",
                headers=admin_auth_data["headers"],
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["success"] is True
            assert data["dialect"] == "POSTGRESQL"
            assert isinstance(data["latency_ms"], (int, float))
            assert "diagnostic_details" in data
            assert data["diagnostic_details"] is not None
            assert "tcp_reachable" in data["diagnostic_details"]
            assert data["diagnostic_details"]["tcp_reachable"] is True
            assert "ssl_active" in data["diagnostic_details"]
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_trigger_manual_sync_endpoint(
    test_db_session: AsyncSession, admin_auth_data: dict, sample_connection: DatabaseConnection
):
    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Trigger manual sync -> 202 Accepted
            resp = await client.post(
                f"/api/v1/connections/{sample_connection.id}/sync",
                headers=admin_auth_data["headers"],
            )
            assert resp.status_code == 202, resp.text
            job_data = resp.json()
            assert job_data["connection_id"] == str(sample_connection.id)
            assert job_data["status"] == "QUEUED"
            assert job_data["triggered_by"] == "MANUAL"
            assert "id" in job_data

            # 2. Concurrency check: Triggering sync while a job is running or queued -> 409 Conflict
            resp_conflict = await client.post(
                f"/api/v1/connections/{sample_connection.id}/sync",
                headers=admin_auth_data["headers"],
            )
            assert resp_conflict.status_code == 409, resp_conflict.text
            assert "already" in resp_conflict.json()["detail"].lower()
    finally:
        app.dependency_overrides.pop(get_db_session, None)
