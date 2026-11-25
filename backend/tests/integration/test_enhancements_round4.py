from __future__ import annotations

import uuid
import pytest
from httpx import ASGITransport, AsyncClient
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
from backend.src.services.auth.jwt_service import auth_service
from backend.src.services.crypto.envelope import EnvelopeEncryptionService


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
async def test_discover_databases_endpoint(admin_auth_data):
    """Asserts POST /api/v1/connections/discover-databases endpoint connects transiently and discovers databases."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Invalid host test
        payload_fail = {
            "engine_type": "POSTGRESQL",
            "host": "127.0.0.1",
            "port": 54399,
            "credentials": {"username": "postgres", "password": "wrongpassword"},
            "custom_params": {},
        }
        res_fail = await client.post(
            "/api/v1/connections/discover-databases",
            json=payload_fail,
            headers=admin_auth_data["headers"],
        )
        assert res_fail.status_code == 200
        data_fail = res_fail.json()
        assert data_fail["success"] is False
        assert "databases" in data_fail
        assert isinstance(data_fail["databases"], list)

        # Real test on active postgres
        payload_ok = {
            "engine_type": "POSTGRESQL",
            "host": "127.0.0.1",
            "port": 5432,
            "credentials": {"username": "postgres", "password": "12345678"},
            "custom_params": {},
        }
        res_ok = await client.post(
            "/api/v1/connections/discover-databases",
            json=payload_ok,
            headers=admin_auth_data["headers"],
        )
        assert res_ok.status_code == 200
        data_ok = res_ok.json()
        if data_ok["success"]:
            assert len(data_ok["databases"]) > 0
            assert "latency_ms" in data_ok


@pytest.mark.asyncio
async def test_test_unsaved_connection_endpoint(admin_auth_data):
    """Asserts POST /api/v1/connections/test-unsaved runs 5-point diagnostic test before saving."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "engine_type": "POSTGRESQL",
            "host": "127.0.0.1",
            "port": 5432,
            "database_name": "enterprise_datacatalog",
            "credentials": {"username": "postgres", "password": "12345678"},
            "custom_params": {},
        }
        res = await client.post(
            "/api/v1/connections/test-unsaved",
            json=payload,
            headers=admin_auth_data["headers"],
        )
        assert res.status_code == 200
        data = res.json()
        assert "success" in data
        assert "latency_ms" in data
        assert "dialect" in data
        assert data["dialect"] == "POSTGRESQL"


@pytest.mark.asyncio
async def test_physical_tree_filtering_and_fields(test_db_session: AsyncSession, admin_auth_data):
    """Asserts GET /api/v1/tables/tree/physical supports search/engine/host/owner filters and includes host, port, owner."""
    crypto = EnvelopeEncryptionService()
    enc_dek, enc_creds = crypto.encrypt_credentials({"username": "u", "password": "p"})

    suffix = uuid.uuid4().hex[:6]
    conn_name = f"finance_analytics_db_{suffix}"
    host_addr = f"192.168.10.{int(suffix[:2], 16) % 250 + 1}"

    conn = DatabaseConnection(
        id=uuid.uuid4(),
        name=conn_name,
        engine_type=DatabaseEngine.POSTGRESQL,
        host=host_addr,
        port=5432,
        database_name=f"finance_db_{suffix}",
        owner=f"finance-team-{suffix}",
        is_active=True,
        encrypted_dek=enc_dek,
        encrypted_credentials=enc_creds,
    )
    test_db_session.add(conn)
    schema = DatabaseSchema(
        id=uuid.uuid4(),
        connection_id=conn.id,
        name="public",
        status=LifecycleStatus.ACTIVE,
    )
    test_db_session.add(schema)
    table = CatalogTable(
        id=uuid.uuid4(),
        schema_id=schema.id,
        name="ledgers",
        table_type=TableType.TABLE,
        status=LifecycleStatus.ACTIVE,
    )
    test_db_session.add(table)
    await test_db_session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Filter by host
        res_host = await client.get(
            f"/api/v1/tables/tree/physical?host={host_addr}",
            headers=admin_auth_data["headers"],
        )
        assert res_host.status_code == 200
        items_host = res_host.json()
        assert any(item["name"] == conn_name for item in items_host)
        matched = next(item for item in items_host if item["name"] == conn_name)
        assert matched["host"] == host_addr
        assert matched["port"] == 5432
        assert matched["owner"] == f"finance-team-{suffix}"

        # 2. Filter by search
        res_search = await client.get(
            f"/api/v1/tables/tree/physical?search={conn_name}",
            headers=admin_auth_data["headers"],
        )
        assert res_search.status_code == 200
        items_search = res_search.json()
        assert any(item["name"] == conn_name for item in items_search)

        # 3. Filter by nonexistent
        res_none = await client.get(
            "/api/v1/tables/tree/physical?search=nonexistent_xyz_db",
            headers=admin_auth_data["headers"],
        )
        assert res_none.status_code == 200
        items_none = res_none.json()
        assert len(items_none) == 0

        # 4. Filter by valid engine dialect with lowercase/normalization
        res_engine = await client.get(
            "/api/v1/tables/tree/physical?engine_type=postgresql",
            headers=admin_auth_data["headers"],
        )
        assert res_engine.status_code == 200

        # 5. Filter by invalid engine dialect returns HTTP 422 Unprocessable Entity
        res_invalid_engine = await client.get(
            "/api/v1/tables/tree/physical?engine_type=INVALID_SQL_ENGINE",
            headers=admin_auth_data["headers"],
        )
        assert res_invalid_engine.status_code == 422
        assert "Invalid engine dialect" in res_invalid_engine.json()["detail"]


@pytest.mark.asyncio
async def test_catalog_objects_endpoint(test_db_session: AsyncSession, admin_auth_data):
    """Asserts GET /api/v1/catalog/objects returns counts and items for tables, views, indexes, routines."""
    crypto = EnvelopeEncryptionService()
    enc_dek, enc_creds = crypto.encrypt_credentials({"username": "u", "password": "p"})

    conn = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"objects_test_conn_{uuid.uuid4().hex[:6]}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host="127.0.0.1",
        port=5432,
        database_name="objects_test_db",
        owner="core-team",
        is_active=True,
        encrypted_dek=enc_dek,
        encrypted_credentials=enc_creds,
    )
    test_db_session.add(conn)
    schema = DatabaseSchema(
        id=uuid.uuid4(),
        connection_id=conn.id,
        name="crm",
        status=LifecycleStatus.ACTIVE,
    )
    test_db_session.add(schema)

    tbl = CatalogTable(
        id=uuid.uuid4(),
        schema_id=schema.id,
        name="customers",
        table_type=TableType.TABLE,
        source_description="Source customers",
        approved_description="Approved customers",
        description_source="AI_APPROVED",
        status=LifecycleStatus.ACTIVE,
    )
    test_db_session.add(tbl)

    view = CatalogTable(
        id=uuid.uuid4(),
        schema_id=schema.id,
        name="active_customers_v",
        table_type=TableType.VIEW,
        source_description="Active customers view",
        status=LifecycleStatus.ACTIVE,
    )
    test_db_session.add(view)

    idx = CatalogIndex(
        id=uuid.uuid4(),
        table_id=tbl.id,
        schema_id=schema.id,
        name="idx_customers_email",
        is_unique=True,
        is_primary=False,
        index_type="BTREE",
        columns_list=["email"],
        definition="CREATE UNIQUE INDEX idx_customers_email ON crm.customers USING btree (email)",
    )
    test_db_session.add(idx)

    proc = CatalogRoutine(
        id=uuid.uuid4(),
        schema_id=schema.id,
        name="sp_archive_customers",
        routine_type="PROCEDURE",
        language="plpgsql",
        arguments_signature="(p_before_date date)",
        return_type="void",
        source_description="Archive customer routine",
    )
    test_db_session.add(proc)

    func_obj = CatalogRoutine(
        id=uuid.uuid4(),
        schema_id=schema.id,
        name="fn_calculate_ltv",
        routine_type="FUNCTION",
        language="sql",
        arguments_signature="(p_customer_id bigint)",
        return_type="numeric",
        source_description="LTV calculation function",
    )
    test_db_session.add(func_obj)
    await test_db_session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get(
            f"/api/v1/catalog/objects?database={conn.name}&schema=crm",
            headers=admin_auth_data["headers"],
        )
        assert res.status_code == 200
        data = res.json()
        assert "counts" in data
        assert data["counts"]["tables"] >= 1
        assert data["counts"]["views"] >= 1
        assert data["counts"]["indexes"] >= 1
        assert data["counts"]["procedures"] >= 1
        assert data["counts"]["functions"] >= 1

        assert any(t["name"] == "customers" for t in data["tables"])
        assert any(v["name"] == "active_customers_v" for v in data["views"])
        assert any(i["name"] == "idx_customers_email" for i in data["indexes"])
        assert any(p["name"] == "sp_archive_customers" for p in data["procedures"])
        assert any(f["name"] == "fn_calculate_ltv" for f in data["functions"])


@pytest.mark.asyncio
async def test_reports_descriptions_and_preview(test_db_session: AsyncSession, admin_auth_data):
    """Asserts preview table and exports include both source and approved descriptions with description_source."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Preview endpoint
        res_prev = await client.get("/api/v1/reports/preview", headers=admin_auth_data["headers"])
        assert res_prev.status_code == 200
        items_prev = res_prev.json()
        assert isinstance(items_prev, list)

        # JSON export
        res_json = await client.get("/api/v1/reports/export?format=json", headers=admin_auth_data["headers"])
        assert res_json.status_code == 200
        data_json = res_json.json()
        assert "tables" in data_json
        assert "columns" in data_json
        if data_json["tables"]:
            tbl_sample = data_json["tables"][0]
            assert "source_description" in tbl_sample
            assert "approved_description" in tbl_sample
            assert "description_source" in tbl_sample
