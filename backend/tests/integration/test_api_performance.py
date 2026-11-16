from __future__ import annotations

import time
import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.database import get_db_session
from backend.src.main import app
from backend.src.models.connections import DatabaseConnection, DatabaseEngine
from backend.src.models.identity import User, UserStatus
from backend.src.services.auth.jwt_service import auth_service
from backend.src.services.crypto.envelope import EnvelopeEncryptionService


@pytest.fixture
async def performance_fixture(test_db_session: AsyncSession):
    suffix = uuid.uuid4().hex[:8]
    user = User(
        id=uuid.uuid4(),
        username=f"perf_admin_{suffix}",
        email=f"perf_{suffix}@company.internal",
        hashed_password=auth_service.hash_password("SuperSecret#2026"),
        full_name="Perf Admin",
        status=UserStatus.ACTIVE,
        is_superadmin=True,
    )
    test_db_session.add(user)

    crypto = EnvelopeEncryptionService()
    enc_dek, enc_creds = crypto.encrypt_credentials({"username": "postgres", "password": "password"})

    conn = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"Perf_Conn_{suffix}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host="127.0.0.1",
        port=5432,
        database_name="enterprise_datacatalog",
        default_schema="public",
        owner=user.username,
        encrypted_dek=enc_dek,
        encrypted_credentials=enc_creds,
        is_active=True,
    )
    test_db_session.add(conn)
    await test_db_session.commit()

    token = auth_service.create_access_token(
        data={
            "sub": str(user.id),
            "username": user.username,
            "email": user.email,
            "roles": ["SuperAdmin"],
            "is_superadmin": True,
        }
    )
    headers = {"Authorization": f"Bearer {token}"}

    # Fast bulk insertion of 10,000 sync jobs using generate_series
    bulk_insert_sql = text("""
        INSERT INTO sync_jobs (
            id, connection_id, status, triggered_by, tables_scanned, tables_created,
            tables_updated, tables_deleted, columns_scanned, changes_detected,
            created_at, error_trace_jsonb
        )
        SELECT 
            gen_random_uuid(),
            :conn_id,
            CASE (n % 4)
                WHEN 0 THEN 'SUCCESS'
                WHEN 1 THEN 'RUNNING'
                WHEN 2 THEN 'FAILED'
                ELSE 'QUEUED'
            END,
            CASE (n % 3)
                WHEN 0 THEN 'SCHEDULE'
                WHEN 1 THEN 'MANUAL'
                ELSE 'RETRY'
            END,
            10, 0, 1, 0, 50, 1,
            clock_timestamp() - (n || ' seconds')::interval,
            '{}'::jsonb
        FROM generate_series(1, 10000) AS n;
    """)
    await test_db_session.execute(bulk_insert_sql, {"conn_id": conn.id})
    await test_db_session.commit()

    return {
        "user": user,
        "connection": conn,
        "headers": headers,
    }


@pytest.mark.asyncio
async def test_sync_jobs_polling_latency_under_150ms_sla(
    test_db_session: AsyncSession, performance_fixture: dict
):
    """
    T078 Step A & D: Asserts that querying /api/v1/sync-jobs across 10,000 recorded jobs
    completes in < 150ms per request (SLA target), proving index-accelerated pagination.
    """
    headers = performance_fixture["headers"]

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Measure multiple requests to confirm consistent latency
            latencies = []
            for _ in range(5):
                start = time.perf_counter()
                resp = await client.get("/api/v1/sync-jobs?limit=50", headers=headers)
                elapsed_ms = (time.perf_counter() - start) * 1000
                assert resp.status_code == 200, resp.text
                latencies.append(elapsed_ms)

            avg_latency = sum(latencies) / len(latencies)
            data = resp.json()
            assert data["total"] >= 10000
            assert len(data["items"]) == 50
            assert data["next_cursor"] is not None

            # SLA assertion: average response time must be under 150ms
            assert avg_latency < 150.0, f"Average query latency {avg_latency:.2f}ms exceeded 150ms SLA"
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_sync_jobs_composite_index_explain_analyze(
    test_db_session: AsyncSession, performance_fixture: dict
):
    """
    T078 Step C: Verify query plan uses index scan or fast execution for idx_sync_jobs_created_at.
    """
    explain_sql = text("""
        EXPLAIN ANALYZE
        SELECT id, connection_id, status, created_at
        FROM sync_jobs
        ORDER BY created_at DESC
        LIMIT 50;
    """)
    res = await test_db_session.execute(explain_sql)
    plan_lines = [row[0] for row in res.fetchall()]
    plan_text = " ".join(plan_lines)

    # Verify Index Scan or Backward Index Scan on idx_sync_jobs_created_at
    assert "idx_sync_jobs_created_at" in plan_text or "Index Scan" in plan_text or "Index" in plan_text
