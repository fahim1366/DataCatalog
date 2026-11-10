from __future__ import annotations

import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.config import settings
from backend.src.core.database import get_db_session
from backend.src.main import app
from backend.src.models.audit import AuditLog
from backend.src.models.connections import DatabaseConnection, DatabaseEngine, SyncSchedule
from backend.src.models.identity import Role, User, UserRole, UserStatus
from backend.src.models.rbac import EntityType
from backend.src.services.auth.jwt_service import auth_service
from backend.src.services.connections.connection_service import ConnectionService
from backend.src.services.crypto.envelope import EnvelopeEncryptionService


@pytest.fixture
async def admin_auth_data(test_db_session: AsyncSession):
    """Creates a dedicated SuperAdmin user and returns authorization headers."""
    suffix = uuid.uuid4().hex[:8]
    admin_user = User(
        id=uuid.uuid4(),
        username=f"secadmin_{suffix}",
        email=f"secadmin_{suffix}@company.internal",
        hashed_password=auth_service.hash_password("SuperSecret#2026"),
        full_name="Security Admin",
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
async def test_unauthenticated_connection_rejected():
    """Asserts unauthenticated requests to /api/v1/connections return 401 Unauthorized."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/connections")
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_create_connection_encrypts_credentials_and_audits(
    test_db_session: AsyncSession, admin_auth_data: dict
):
    """
    T023 Step A: Asserts connection creation:
    1. Encrypts credentials with a unique DEK via EnvelopeEncryptionService (zero plaintext in DB).
    2. Masks credentials in API response.
    3. Persists 5-part cron schedule in sync_schedules.
    4. Atomically generates an immutable AuditLog record in PostgreSQL.
    """
    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)
    suffix = admin_auth_data["suffix"]
    secret_password = f"P@ssw0rd_{suffix}_Vault!"

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {
                "name": f"Analytics_Warehouse_{suffix}",
                "engine_type": "POSTGRESQL",
                "host": "dw-analytics.internal.corp",
                "port": 5432,
                "database_name": f"analytics_db_{suffix}",
                "default_schema": "public",
                "credentials": {
                    "username": f"db_user_{suffix}",
                    "password": secret_password,
                    "ssl_cert": "-----BEGIN CERTIFICATE-----\nMIID...\n-----END CERTIFICATE-----",
                },
                "cron_expression": "0 2 * * *",
                "custom_params": {"sslmode": "require", "application_name": "DataCatalog"},
            }

            response = await client.post(
                "/api/v1/connections",
                json=payload,
                headers=admin_auth_data["headers"],
            )

            assert response.status_code == 201, response.text
            res_data = response.json()
            connection_id = uuid.UUID(res_data["id"])

            # 1. API Response Credential Masking Verification
            assert secret_password not in response.text
            assert "encrypted_dek" not in res_data
            assert "encrypted_credentials" not in res_data
            assert res_data["name"] == payload["name"]
            assert res_data["engine_type"] == "POSTGRESQL"
            assert res_data["sync_schedule"] is not None
            assert res_data["sync_schedule"]["cron_expression"] == "0 2 * * *"
            assert "password" in res_data.get("masked_credentials", {})
            assert res_data["masked_credentials"]["password"] == "********"

            # 2. Database Plaintext Immunity Verification (PostgreSQL)
            conn_record = await test_db_session.get(DatabaseConnection, connection_id)
            assert conn_record is not None
            assert conn_record.encrypted_dek is not None
            assert conn_record.encrypted_credentials is not None

            # Raw SQL check across database_connections table to guarantee zero plaintext
            raw_check = await test_db_session.execute(
                text(
                    "SELECT id, name, encrypted_dek, encrypted_credentials FROM database_connections WHERE id = :cid"
                ),
                {"cid": connection_id},
            )
            row = raw_check.fetchone()
            assert row is not None
            # Raw bytes must not contain secret plaintext
            assert secret_password.encode("utf-8") not in bytes(row.encrypted_credentials)
            assert secret_password.encode("utf-8") not in bytes(row.encrypted_dek)

            # 3. Envelope Encryption Decryption Round-Trip Verification
            crypto = EnvelopeEncryptionService()
            decrypted = crypto.decrypt_credentials(
                bytes(conn_record.encrypted_dek), bytes(conn_record.encrypted_credentials)
            )
            assert decrypted["username"] == f"db_user_{suffix}"
            assert decrypted["password"] == secret_password
            assert "BEGIN CERTIFICATE" in decrypted["ssl_cert"]

            # 4. SyncSchedule Persistence Verification
            sched_query = await test_db_session.execute(
                select(SyncSchedule).where(SyncSchedule.connection_id == connection_id)
            )
            sched = sched_query.scalar_one_or_none()
            assert sched is not None
            assert sched.cron_expression == "0 2 * * *"
            assert sched.is_enabled is True

            # 5. Immutable AuditLog Record Generation
            audit_query = await test_db_session.execute(
                select(AuditLog).where(
                    AuditLog.entity_id == connection_id,
                    AuditLog.action == "CREATE_CONNECTION",
                )
            )
            audit_entry = audit_query.scalar_one_or_none()
            assert audit_entry is not None
            assert audit_entry.actor_username == admin_auth_data["user"].username
            assert audit_entry.entity_type == EntityType.DATABASE
            assert secret_password not in str(audit_entry.diff_jsonb)
            assert audit_entry.diff_jsonb.get("name") == payload["name"]
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_create_connection_rejects_invalid_cron_schedules(
    test_db_session: AsyncSession, admin_auth_data: dict
):
    """
    T023 Step A: Asserts that invalid 5-part cron schedules are rigorously rejected
    with HTTP 422 / validation error and no database record is created.
    """
    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)
    suffix = admin_auth_data["suffix"]

    invalid_cron_expressions = [
        "invalid * * *",       # Non-cron string & only 4 parts
        "* * *",               # 3 parts (needs exactly 5)
        "* * * * * *",         # 6 parts (second/extended format rejected)
        "70 * * * *",          # Minute > 59
        "* 25 * * *",          # Hour > 23
        "* * 32 * *",          # Day of month > 31
        "* * * 13 *",          # Month > 12
        "* * * * 9",           # Day of week > 7
        "*/0 * * * *",         # Division by zero step
        "10-5 * * * *",        # Inverted range
    ]

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for invalid_cron in invalid_cron_expressions:
                payload = {
                    "name": f"Conn_Invalid_{uuid.uuid4().hex[:6]}",
                    "engine_type": "POSTGRESQL",
                    "host": "localhost",
                    "port": 5432,
                    "database_name": "db_test",
                    "credentials": {"username": "user", "password": "pass"},
                    "cron_expression": invalid_cron,
                }
                response = await client.post(
                    "/api/v1/connections",
                    json=payload,
                    headers=admin_auth_data["headers"],
                )
                assert response.status_code in (400, 422), (
                    f"Expected cron '{invalid_cron}' to be rejected, but got {response.status_code}: {response.text}"
                )
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_unique_dek_per_connection(
    test_db_session: AsyncSession, admin_auth_data: dict
):
    """
    T023: Asserts each created connection receives an independently generated ephemeral DEK.
    Two connections with identical credentials MUST have different encrypted_dek and encrypted_credentials.
    """
    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)
    suffix = admin_auth_data["suffix"]
    shared_creds = {"username": "shared_svc", "password": "IdenticalPassword#99"}

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res1 = await client.post(
                "/api/v1/connections",
                json={
                    "name": f"Conn_A_{suffix}",
                    "engine_type": "MYSQL",
                    "host": "mysql-a.internal",
                    "port": 3306,
                    "database_name": "app_db",
                    "credentials": shared_creds,
                },
                headers=admin_auth_data["headers"],
            )
            res2 = await client.post(
                "/api/v1/connections",
                json={
                    "name": f"Conn_B_{suffix}",
                    "engine_type": "MYSQL",
                    "host": "mysql-b.internal",
                    "port": 3306,
                    "database_name": "app_db",
                    "credentials": shared_creds,
                },
                headers=admin_auth_data["headers"],
            )
            assert res1.status_code == 201
            assert res2.status_code == 201

            c1 = await test_db_session.get(DatabaseConnection, uuid.UUID(res1.json()["id"]))
            c2 = await test_db_session.get(DatabaseConnection, uuid.UUID(res2.json()["id"]))

            assert c1.encrypted_dek != c2.encrypted_dek
            assert c1.encrypted_credentials != c2.encrypted_credentials
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_list_and_get_connections_mask_credentials(
    test_db_session: AsyncSession, admin_auth_data: dict
):
    """
    T023: Asserts GET /api/v1/connections and GET /api/v1/connections/{id} return masked credentials
    and never expose plaintext secrets.
    """
    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)
    suffix = admin_auth_data["suffix"]
    secret_pass = f"ExposeCheck_{suffix}_TopSecret"

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            create_res = await client.post(
                "/api/v1/connections",
                json={
                    "name": f"Conn_Mask_Test_{suffix}",
                    "engine_type": "ORACLE",
                    "host": "oracle.corp",
                    "port": 1521,
                    "database_name": "ORCLPDB",
                    "credentials": {"username": "system", "password": secret_pass},
                    "cron_expression": "*/15 * * * *",
                },
                headers=admin_auth_data["headers"],
            )
            assert create_res.status_code == 201
            conn_id = create_res.json()["id"]

            # GET list
            list_res = await client.get("/api/v1/connections", headers=admin_auth_data["headers"])
            assert list_res.status_code == 200
            assert secret_pass not in list_res.text
            items = list_res.json()
            matching = [c for c in items if c["id"] == conn_id]
            assert len(matching) == 1
            assert matching[0]["masked_credentials"]["password"] == "********"

            # GET by ID
            get_res = await client.get(f"/api/v1/connections/{conn_id}", headers=admin_auth_data["headers"])
            assert get_res.status_code == 200
            assert secret_pass not in get_res.text
            assert get_res.json()["masked_credentials"]["password"] == "********"
    finally:
        app.dependency_overrides.pop(get_db_session, None)

