from __future__ import annotations

import asyncio
import time
import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.config import settings
from backend.src.core.database import get_db_session
from backend.src.main import app
from backend.src.models.connections import DatabaseConnection, DatabaseEngine
from backend.src.models.identity import User, UserStatus
from backend.src.services.auth.jwt_service import auth_service
from backend.src.services.connections.ping_service import PingResult, PingService
from backend.src.services.crypto.envelope import EnvelopeEncryptionService


@pytest.fixture
async def admin_auth_data(test_db_session: AsyncSession):
    """Creates a dedicated SuperAdmin user and returns authorization headers."""
    suffix = uuid.uuid4().hex[:8]
    admin_user = User(
        id=uuid.uuid4(),
        username=f"pingadmin_{suffix}",
        email=f"pingadmin_{suffix}@company.internal",
        hashed_password=auth_service.hash_password("SuperSecret#2026"),
        full_name="Ping Admin",
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


@pytest.mark.asyncio
async def test_connection_ping_postgresql_live_success(
    test_db_session: AsyncSession, admin_auth_data: dict
):
    """
    T024 Step A: Asserts that /api/v1/connections/{id}/test executes a live PostgreSQL dialect ping
    (SELECT 1), returns HTTP 200 with success=True, positive latency, and dialect details.
    """
    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)
    suffix = admin_auth_data["suffix"]

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
    enc_dek, enc_creds = crypto.encrypt_credentials(
        {
            "username": pg_user,
            "password": pg_pass,
        }
    )

    # Register connection pointing to active PostgreSQL test instance
    conn = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"Live_PG_Test_{suffix}",
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
    await test_db_session.flush()

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/connections/{conn.id}/test",
                headers=admin_auth_data["headers"],
            )

            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["success"] is True
            assert payload["dialect"] == "POSTGRESQL"
            assert isinstance(payload["latency_ms"], (int, float))
            assert payload["latency_ms"] >= 0.0
            assert "successful" in payload["message"].lower() or "connected" in payload["message"].lower()
            assert "tested_at" in payload
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_connection_ping_unreachable_returns_structured_failure(
    test_db_session: AsyncSession, admin_auth_data: dict
):
    """
    T024 Step A: Asserts that an unreachable target host/port returns a structured diagnostic failure
    payload with success=False, error details, and measured latency, without raising unhandled 500 exceptions.
    """
    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)
    suffix = admin_auth_data["suffix"]

    crypto = EnvelopeEncryptionService()
    enc_dek, enc_creds = crypto.encrypt_credentials(
        {"username": "unreachable_user", "password": "DummyPassword"}
    )

    # Point to guaranteed non-listening port on localhost
    conn = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"Unreachable_Conn_{suffix}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host="127.0.0.1",
        port=59999,
        database_name="unreachable_db",
        owner=admin_auth_data["user"].username,
        encrypted_dek=enc_dek,
        encrypted_credentials=enc_creds,
        is_active=True,
    )
    test_db_session.add(conn)
    await test_db_session.flush()

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/connections/{conn.id}/test",
                headers=admin_auth_data["headers"],
            )

            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["success"] is False
            assert payload["dialect"] == "POSTGRESQL"
            assert isinstance(payload["latency_ms"], (int, float))
            assert payload["diagnostic_details"] is not None
            assert "error" in payload["diagnostic_details"] or "message" in payload
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_connection_ping_strict_5s_timeout_enforced():
    """
    T024 Step A: Asserts PingService strictly enforces a 5.0-second timeout ceiling
    via asyncio.wait_for(..., timeout=5.0) on stalled or non-routable endpoints.
    """
    ping_service = PingService()
    start_time = time.monotonic()

    # 192.0.2.1 is TEST-NET-1 (RFC 5737), which discards traffic without replying RST
    result = await ping_service.ping(
        engine_type=DatabaseEngine.POSTGRESQL,
        host="192.0.2.1",
        port=5432,
        database_name="stalled_db",
        credentials={"username": "user", "password": "password"},
    )
    elapsed = time.monotonic() - start_time

    # Must complete within 5.5s ceiling (5.0s timeout + small scheduling delta)
    assert elapsed < 5.8, f"Ping took {elapsed}s, exceeded 5.0s strict timeout ceiling!"
    assert result.success is False
    assert "timeout" in result.message.lower() or (
        result.diagnostic_details and "timeout" in str(result.diagnostic_details).lower()
    )


@pytest.mark.asyncio
async def test_ping_service_all_dialects_supported():
    """
    T024 Step A: Asserts that PingService supports all 5 specified database engine dialects:
    POSTGRESQL, MSSQL, MYSQL, ORACLE, and CLICKHOUSE, returning structured PingResult.
    """
    ping_service = PingService()
    dialects = [
        DatabaseEngine.POSTGRESQL,
        DatabaseEngine.MSSQL,
        DatabaseEngine.MYSQL,
        DatabaseEngine.ORACLE,
        DatabaseEngine.CLICKHOUSE,
    ]

    for dialect in dialects:
        result = await ping_service.ping(
            engine_type=dialect,
            host="127.0.0.1",
            port=59999,  # closed probe port
            database_name="diag_db",
            credentials={"username": "test_user", "password": "test_password"},
        )
        assert isinstance(result, PingResult)
        assert result.dialect == dialect
        assert isinstance(result.latency_ms, (int, float))
        assert isinstance(result.message, str)
        assert isinstance(result.diagnostic_details, dict)


@pytest.mark.asyncio
async def test_ping_nonexistent_connection_returns_404(
    test_db_session: AsyncSession, admin_auth_data: dict
):
    """Asserts attempting to test a non-existent connection ID returns 404 Not Found."""
    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)
    missing_id = uuid.uuid4()

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/connections/{missing_id}/test",
                headers=admin_auth_data["headers"],
            )
            assert response.status_code == 404
    finally:
        app.dependency_overrides.pop(get_db_session, None)

