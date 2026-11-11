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
    CatalogTable,
    ClassificationLevel,
    DatabaseSchema,
    LifecycleStatus,
    TableType,
)
from backend.src.models.connections import DatabaseConnection, DatabaseEngine
from backend.src.models.identity import User, UserStatus
from backend.src.services.auth.jwt_service import auth_service
from backend.src.services.catalog.version_service import version_service


@pytest.fixture
async def version_api_data(test_db_session: AsyncSession):
    """Sets up an admin user, catalog table, two version snapshots, and JWT auth."""
    suffix = uuid.uuid4().hex[:8]

    # Create Admin User
    admin = User(
        id=uuid.uuid4(),
        username=f"admin_diff_{suffix}",
        email=f"admin_diff_{suffix}@company.internal",
        hashed_password=auth_service.hash_password("AdminPass123!"),
        full_name="Admin Diff Tester",
        status=UserStatus.ACTIVE,
        is_superadmin=True,
    )
    test_db_session.add(admin)

    # Create Database Connection & Schema
    connection = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"conn_diff_{suffix}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host="localhost",
        port=5432,
        database_name=f"db_{suffix}",
        owner=admin.username,
        kek_version=1,
        encrypted_dek=b"dummy_dek_32_bytes_data_contract",
        encrypted_credentials=b"dummy_creds_contract_test",
    )
    test_db_session.add(connection)
    await test_db_session.flush()

    schema = DatabaseSchema(
        id=uuid.uuid4(),
        connection_id=connection.id,
        name=f"diff_schema_{suffix}",
        owner=admin.username,
    )
    test_db_session.add(schema)
    await test_db_session.flush()

    # Create Table
    table = CatalogTable(
        id=uuid.uuid4(),
        schema_id=schema.id,
        name=f"orders_{suffix}",
        table_type=TableType.TABLE,
        row_count_estimate=1000,
        size_bytes=40960,
        source_description="Orders table",
        approved_description="Curated orders table",
        owner=admin.username,
        status=LifecycleStatus.ACTIVE,
    )
    test_db_session.add(table)
    await test_db_session.flush()

    # Initial Columns for Version 1
    col_id = CatalogColumn(
        id=uuid.uuid4(),
        table_id=table.id,
        name="order_id",
        ordinal_position=1,
        data_type="BIGINT",
        raw_data_type="bigint",
        is_nullable=False,
        is_primary_key=True,
        is_foreign_key=False,
        classification_level=ClassificationLevel.INTERNAL,
        status=LifecycleStatus.ACTIVE,
    )
    col_notes = CatalogColumn(
        id=uuid.uuid4(),
        table_id=table.id,
        name="notes",
        ordinal_position=2,
        data_type="VARCHAR(200)",
        raw_data_type="varchar(200)",
        is_nullable=True,
        is_primary_key=False,
        is_foreign_key=False,
        classification_level=ClassificationLevel.INTERNAL,
        status=LifecycleStatus.ACTIVE,
    )
    col_status = CatalogColumn(
        id=uuid.uuid4(),
        table_id=table.id,
        name="status",
        ordinal_position=3,
        data_type="VARCHAR(20)",
        raw_data_type="varchar(20)",
        is_nullable=False,
        is_primary_key=False,
        is_foreign_key=False,
        classification_level=ClassificationLevel.INTERNAL,
        status=LifecycleStatus.ACTIVE,
    )
    test_db_session.add_all([col_id, col_notes, col_status])
    await test_db_session.flush()

    # Capture Version 1
    v1 = await version_service.capture_version(
        session=test_db_session,
        table_id=table.id,
        actor_id=admin.id,
        actor_username=admin.username,
    )

    # Mutate for Version 2:
    # 1. Drop col_notes
    col_notes.status = LifecycleStatus.DELETED

    # 2. Modify col_status data type
    col_status.data_type = "TEXT"
    col_status.raw_data_type = "text"

    # 3. Add col_total
    col_total = CatalogColumn(
        id=uuid.uuid4(),
        table_id=table.id,
        name="total_amount",
        ordinal_position=4,
        data_type="NUMERIC(12,2)",
        raw_data_type="numeric(12,2)",
        is_nullable=False,
        is_primary_key=False,
        is_foreign_key=False,
        classification_level=ClassificationLevel.INTERNAL,
        status=LifecycleStatus.ACTIVE,
    )
    test_db_session.add(col_total)
    await test_db_session.flush()

    # Capture Version 2
    v2 = await version_service.capture_version_if_changed(
        session=test_db_session,
        table_id=table.id,
        actor_id=admin.id,
        actor_username=admin.username,
    )

    token = auth_service.create_access_token(
        data={
            "sub": str(admin.id),
            "username": admin.username,
            "email": admin.email,
            "roles": ["SuperAdmin"],
            "is_superadmin": True,
        }
    )

    return {
        "admin": admin,
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"},
        "table": table,
        "v1": v1,
        "v2": v2,
    }


@pytest.mark.asyncio
async def test_unauthenticated_version_endpoints_return_401():
    """Asserts unauthenticated requests to version endpoints return 401 Unauthorized."""
    transport = ASGITransport(app=app)
    table_id = uuid.uuid4()
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res1 = await client.get(f"/api/v1/tables/{table_id}/versions")
        assert res1.status_code == 401

        res2 = await client.get(f"/api/v1/tables/{table_id}/versions/compare?v1=1&v2=2")
        assert res2.status_code == 401


@pytest.mark.asyncio
async def test_list_table_versions(
    test_db_session: AsyncSession, version_api_data: dict
):
    """
    T030: Asserts GET /api/v1/tables/{table_id}/versions returns list of TableVersionItems.
    """
    data = version_api_data
    table = data["table"]
    headers = data["headers"]

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/api/v1/tables/{table.id}/versions",
                headers=headers,
            )
            assert response.status_code == 200
            versions = response.json()
            assert isinstance(versions, list)
            assert len(versions) == 2

            v_nums = [v["version_num"] for v in versions]
            assert 1 in v_nums
            assert 2 in v_nums

            # Contract schema check
            for v in versions:
                assert "id" in v
                assert "table_id" in v
                assert "version_num" in v
                assert "created_at" in v
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_version_compare_contract(
    test_db_session: AsyncSession, version_api_data: dict
):
    """
    T030: Asserts GET /api/v1/tables/{table_id}/versions/compare?v1=1&v2=2 returns:
    - added_columns
    - removed_columns
    - modified_columns
    matching OpenAPI VersionDiffResponse schema.
    """
    data = version_api_data
    table = data["table"]
    headers = data["headers"]

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/api/v1/tables/{table.id}/versions/compare?v1=1&v2=2",
                headers=headers,
            )
            assert response.status_code == 200
            diff = response.json()

            # Root contract keys
            assert diff["table_id"] == str(table.id)
            assert diff["v1_num"] == 1
            assert diff["v2_num"] == 2
            assert "added_columns" in diff
            assert "removed_columns" in diff
            assert "modified_columns" in diff

            # Added columns assertion
            added_names = [col["name"] for col in diff["added_columns"]]
            assert "total_amount" in added_names

            # Removed columns assertion
            removed_names = [col["name"] for col in diff["removed_columns"]]
            assert "notes" in removed_names

            # Modified columns assertion
            modified = {
                mod["column_name"]: mod for mod in diff["modified_columns"]
            }
            assert "status" in modified
            assert modified["status"]["v1_attributes"]["data_type"] == "VARCHAR(20)"
            assert modified["status"]["v2_attributes"]["data_type"] == "TEXT"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_compare_nonexistent_table_or_version_returns_404(
    test_db_session: AsyncSession, version_api_data: dict
):
    """Asserts 404 when table or requested version numbers do not exist."""
    data = version_api_data
    table = data["table"]
    headers = data["headers"]

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Non-existent table
            missing_tbl_id = uuid.uuid4()
            res_missing_tbl = await client.get(
                f"/api/v1/tables/{missing_tbl_id}/versions/compare?v1=1&v2=2",
                headers=headers,
            )
            assert res_missing_tbl.status_code == 404

            # Non-existent version
            res_missing_ver = await client.get(
                f"/api/v1/tables/{table.id}/versions/compare?v1=1&v2=99",
                headers=headers,
            )
            assert res_missing_ver.status_code == 404
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_bulk_ingestion_api_contract(
    test_db_session: AsyncSession, version_api_data: dict
):
    """
    T033: Asserts POST /api/v1/ingestion/bulk executes metadata ingestion,
    returns 200 with BulkIngestionResponse, and handles unauthenticated or invalid calls.
    """
    data = version_api_data
    headers = data["headers"]

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Unauthenticated request returns 401
            unauth_res = await client.post(
                "/api/v1/ingestion/bulk",
                json={"connection_id": str(uuid.uuid4()), "schema_name": "s1", "tables": []},
            )
            assert unauth_res.status_code == 401

            # 2. Non-existent connection returns 404
            missing_conn_res = await client.post(
                "/api/v1/ingestion/bulk",
                headers=headers,
                json={"connection_id": str(uuid.uuid4()), "schema_name": "s1", "tables": []},
            )
            assert missing_conn_res.status_code == 404

            # 3. Successful ingestion returns 200 with BulkIngestionResponse
            connection = (await test_db_session.execute(
                select(DatabaseConnection).limit(1)
            )).scalar_one()

            payload = {
                "connection_id": str(connection.id),
                "schema_name": f"ingest_api_schema_{data['admin'].username}",
                "tables": [
                    {
                        "name": "api_table",
                        "table_type": "TABLE",
                        "row_count_estimate": 100,
                        "size_bytes": 1024,
                        "source_description": "API ingested table",
                        "columns": [
                            {
                                "name": "id",
                                "ordinal_position": 1,
                                "data_type": "INT",
                                "raw_data_type": "int",
                                "is_nullable": False,
                                "is_primary_key": True,
                                "is_foreign_key": False,
                                "source_description": "ID",
                                "classification_level": "INTERNAL",
                            }
                        ],
                    }
                ],
            }

            res = await client.post(
                "/api/v1/ingestion/bulk",
                headers=headers,
                json=payload,
            )
            assert res.status_code == 200
            resp_data = res.json()
            assert resp_data["success"] is True
            assert resp_data["tables_upserted"] == 1
            assert resp_data["columns_upserted"] == 1
            assert resp_data["versions_created"] == 1
    finally:
        app.dependency_overrides.clear()

