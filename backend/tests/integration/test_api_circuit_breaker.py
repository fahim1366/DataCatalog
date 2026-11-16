from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.database import get_db_session
from backend.src.main import app
from backend.src.models.connections import DatabaseConnection, DatabaseEngine, SyncSchedule
from backend.src.models.identity import User, UserStatus
from backend.src.services.auth.jwt_service import auth_service
from backend.src.services.crypto.envelope import EnvelopeEncryptionService


@pytest.fixture
async def admin_auth_data(test_db_session: AsyncSession):
    suffix = uuid.uuid4().hex[:8]
    admin_user = User(
        id=uuid.uuid4(),
        username=f"cbadmin_{suffix}",
        email=f"cbadmin_{suffix}@company.internal",
        hashed_password=auth_service.hash_password("SuperSecret#2026"),
        full_name="CB Admin",
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
async def tripped_connection(test_db_session: AsyncSession, admin_auth_data: dict):
    crypto = EnvelopeEncryptionService()
    enc_dek, enc_creds = crypto.encrypt_credentials({"username": "postgres", "password": "password"})

    conn = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"Tripped_Conn_{admin_auth_data['suffix']}",
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

    now = datetime.now(timezone.utc)
    schedule = SyncSchedule(
        id=uuid.uuid4(),
        connection_id=conn.id,
        cron_expression="0 2 * * *",
        is_enabled=True,
        circuit_state="OPEN",
        consecutive_failures=3,
        cooldown_until=now + timedelta(minutes=15),
    )
    test_db_session.add(schedule)
    await test_db_session.commit()
    await test_db_session.refresh(conn)
    return conn, schedule


@pytest.mark.asyncio
async def test_reset_circuit_breaker_endpoint(
    test_db_session: AsyncSession, admin_auth_data: dict, tripped_connection: tuple
):
    connection, schedule = tripped_connection

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/connections/{connection.id}/circuit-breaker/reset",
                headers=admin_auth_data["headers"],
            )
            assert resp.status_code == 200, resp.text
            payload = resp.json()
            assert payload["connection_id"] == str(connection.id)
            assert payload["circuit_state"] == "CLOSED"
            assert payload["consecutive_failures"] == 0
            assert payload["cooldown_until"] is None

            # Verify in DB
            await test_db_session.refresh(schedule)
            assert schedule.circuit_state == "CLOSED"
            assert schedule.consecutive_failures == 0
            assert schedule.cooldown_until is None

            # Test 404 for non-existent connection
            random_id = uuid.uuid4()
            resp_404 = await client.post(
                f"/api/v1/connections/{random_id}/circuit-breaker/reset",
                headers=admin_auth_data["headers"],
            )
            assert resp_404.status_code == 404
    finally:
        app.dependency_overrides.pop(get_db_session, None)
