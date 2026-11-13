from __future__ import annotations

import time
import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.config import settings
from backend.src.core.database import async_session_factory, get_db_session
from backend.src.main import app
from backend.src.models.audit import AuditLog
from backend.src.models.catalog import (
    CatalogColumn,
    CatalogTable,
    ClassificationLevel,
    DatabaseSchema,
    LifecycleStatus,
    TableType,
)
from backend.src.models.connections import DatabaseConnection, DatabaseEngine
from backend.src.models.identity import Role, User, UserRole, UserStatus
from backend.src.models.rbac import (
    EntityType,
    PermissionAction,
    PermissionEffect,
    PermissionRule,
    ResourceType,
)
from backend.src.schemas.ingestion import (
    BulkIngestionRequest,
    IngestColumnPayload,
    IngestTablePayload,
)
from backend.src.services.auth.jwt_service import auth_service
from backend.src.services.crypto.envelope import EnvelopeEncryptionService
from backend.src.services.ingestion.bulk_ingestion_service import bulk_ingestion_service
from backend.src.services.reporting.report_service import report_service


@pytest.fixture
async def e2e_test_environment(test_db_session: AsyncSession):
    """
    Sets up a complete enterprise test environment with:
    - SuperAdmin user and JWT credentials
    - Analyst user with restricted role
    - Live database connection coordinates for dialect ping testing
    """
    suffix = uuid.uuid4().hex[:8]

    # 1. SuperAdmin User
    admin_user = User(
        id=uuid.uuid4(),
        username=f"admin_{suffix}",
        email=f"admin_{suffix}@company.internal",
        hashed_password=auth_service.hash_password("SuperSecret#2026"),
        full_name="Enterprise SuperAdmin",
        status=UserStatus.ACTIVE,
        is_superadmin=True,
    )
    test_db_session.add(admin_user)

    admin_role = Role(
        id=uuid.uuid4(),
        name=f"SuperAdmin_{suffix}",
        description="Full enterprise administration",
        is_system_role=True,
    )
    test_db_session.add(admin_role)
    await test_db_session.flush()

    test_db_session.add(UserRole(user_id=admin_user.id, role_id=admin_role.id))

    admin_token = auth_service.create_access_token(
        data={
            "sub": str(admin_user.id),
            "username": admin_user.username,
            "email": admin_user.email,
            "roles": [admin_role.name],
            "is_superadmin": True,
        }
    )

    # 2. Analyst User (Restricted RBAC)
    analyst_user = User(
        id=uuid.uuid4(),
        username=f"analyst_{suffix}",
        email=f"analyst_{suffix}@company.internal",
        hashed_password=auth_service.hash_password("AnalystSecret#2026"),
        full_name="Enterprise Analyst",
        status=UserStatus.ACTIVE,
        is_superadmin=False,
    )
    test_db_session.add(analyst_user)

    analyst_role = Role(
        id=uuid.uuid4(),
        name=f"Analyst_{suffix}",
        description="Analyst discovery role",
        is_system_role=False,
    )
    test_db_session.add(analyst_role)
    await test_db_session.flush()

    test_db_session.add(UserRole(user_id=analyst_user.id, role_id=analyst_role.id))

    analyst_token = auth_service.create_access_token(
        data={
            "sub": str(analyst_user.id),
            "username": analyst_user.username,
            "email": analyst_user.email,
            "roles": [analyst_role.name],
            "is_superadmin": False,
        }
    )

    await test_db_session.flush()

    # Extract live test database parameters for dialect ping probe
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

    return {
        "admin_user": admin_user,
        "admin_token": admin_token,
        "admin_headers": {"Authorization": f"Bearer {admin_token}"},
        "analyst_user": analyst_user,
        "analyst_role": analyst_role,
        "analyst_token": analyst_token,
        "analyst_headers": {"Authorization": f"Bearer {analyst_token}"},
        "suffix": suffix,
        "pg_host": pg_host,
        "pg_port": pg_port,
        "pg_user": pg_user,
        "pg_pass": pg_pass,
        "pg_db": pg_db,
    }


# ==============================================================================
# Quickstart Scenario 1: Envelope Encryption & Connection Registry + Dialect Ping
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_1_envelope_encryption_and_connection_registry_ping(
    test_db_session: AsyncSession, e2e_test_environment: dict
):
    """
    Scenario 1 Verification:
    1. Register a PostgreSQL source connection via POST /api/v1/connections with sensitive credentials.
    2. Inspect PostgreSQL database_connections table directly.
    3. Verify encrypted_dek and encrypted_credentials are binary byte arrays.
    4. Verify plaintext password is not present anywhere in database rows or logs.
    5. Query GET /api/v1/connections/{id} and confirm credentials are redacted/masked.
    6. Execute POST /api/v1/connections/{id}/test to trigger dialect ping probe and confirm success.
    """
    env = e2e_test_environment
    suffix = env["suffix"]

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Register connection
            conn_payload = {
                "name": f"e2e_analytics_dw_{suffix}",
                "engine_type": "POSTGRESQL",
                "host": env["pg_host"],
                "port": env["pg_port"],
                "database_name": env["pg_db"],
                "default_schema": "public",
                "owner": env["admin_user"].username,
                "credentials": {
                    "username": env["pg_user"],
                    "password": env["pg_pass"],
                },
                "cron_expression": "0 2 * * *",
            }
            resp = await client.post(
                "/api/v1/connections",
                json=conn_payload,
                headers=env["admin_headers"],
            )
            assert resp.status_code == 201, f"Create connection failed: {resp.text}"
            conn_data = resp.json()
            conn_id = uuid.UUID(conn_data["id"])

            # 2. Inspect database directly
            stmt = select(DatabaseConnection).where(DatabaseConnection.id == conn_id)
            res = await test_db_session.execute(stmt)
            db_conn = res.scalar_one()

            # 3. Verify encrypted_dek and encrypted_credentials are binary byte arrays
            assert isinstance(db_conn.encrypted_dek, (bytes, bytearray))
            # 32-byte DEK encrypted by AES-GCM (12-byte IV + 16-byte auth tag) = 60 bytes
            assert len(db_conn.encrypted_dek) == 60
            assert isinstance(db_conn.encrypted_credentials, (bytes, bytearray))
            assert len(db_conn.encrypted_credentials) > 0

            # 4. Plaintext password not present in raw bytes
            assert env["pg_pass"].encode("utf-8") not in db_conn.encrypted_credentials
            assert env["pg_pass"].encode("utf-8") not in db_conn.encrypted_dek

            # 5. Query GET /api/v1/connections/{id} and confirm credentials are redacted/masked
            get_resp = await client.get(
                f"/api/v1/connections/{conn_id}",
                headers=env["admin_headers"],
            )
            assert get_resp.status_code == 200
            get_data = get_resp.json()
            assert env["pg_pass"] not in get_resp.text
            assert get_data["masked_credentials"]["password"] == "********"

            # 6. Execute POST /api/v1/connections/{id}/test to trigger dialect ping probe
            ping_resp = await client.post(
                f"/api/v1/connections/{conn_id}/test",
                headers=env["admin_headers"],
            )
            assert ping_resp.status_code == 200, f"Ping failed: {ping_resp.text}"
            ping_data = ping_resp.json()
            assert ping_data["success"] is True
            assert ping_data["latency_ms"] > 0
            assert "postgresql" in ping_data["dialect"].lower()
    finally:
        app.dependency_overrides.clear()


# ==============================================================================
# Quickstart Scenario 2: High-Performance Search & Keyset Cursor (< 100ms)
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_2_high_performance_search_and_keyset_navigation(
    test_db_session: AsyncSession, e2e_test_environment: dict
):
    """
    Scenario 2 Verification:
    1. Seed test tables with deterministic searchable names.
    2. Execute /api/v1/search?q=customer_order&limit=10.
    3. Confirm response time is strictly < 100ms.
    4. Extract next_cursor from page_info.
    5. Execute /api/v1/search?q=customer_order&limit=10&cursor={next_cursor}.
    6. Confirm second page returns next set of results without overlaps, also < 100ms.
    """
    env = e2e_test_environment
    suffix = env["suffix"]

    # Seed Connection & Schema
    crypto = EnvelopeEncryptionService()
    enc_dek, enc_creds = crypto.encrypt_credentials({"username": "u", "password": "p"})
    conn = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"search_benchmark_{suffix}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host="localhost",
        port=5432,
        database_name=f"dw_benchmark_{suffix}",
        owner=env["admin_user"].username,
        encrypted_dek=enc_dek,
        encrypted_credentials=enc_creds,
        is_active=True,
    )
    test_db_session.add(conn)

    schema = DatabaseSchema(
        id=uuid.uuid4(),
        connection_id=conn.id,
        name=f"public_bench_{suffix}",
        status=LifecycleStatus.ACTIVE,
    )
    test_db_session.add(schema)
    await test_db_session.flush()

    # Seed 25 customer_order benchmark tables
    for i in range(25):
        tbl = CatalogTable(
            id=uuid.uuid4(),
            schema_id=schema.id,
            name=f"customer_order_{suffix}_{i:03d}",
            table_type=TableType.TABLE,
            source_description=f"Synthetic customer order benchmark table #{i}",
            owner="analytics_team",
            row_count_estimate=500000 + i * 1000,
            size_bytes=10485760 + i * 2048,
            status=LifecycleStatus.ACTIVE,
        )
        test_db_session.add(tbl)
    await test_db_session.flush()

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            search_query = f"customer_order_{suffix}"

            # Page 1: measure latency
            t0 = time.perf_counter()
            resp1 = await client.get(
                f"/api/v1/search?q={search_query}&limit=10",
                headers=env["admin_headers"],
            )
            latency_ms_1 = (time.perf_counter() - t0) * 1000
            assert resp1.status_code == 200, f"Search page 1 failed: {resp1.text}"
            assert latency_ms_1 < 100.0, f"Search page 1 exceeded 100ms SLA: {latency_ms_1:.2f}ms"

            body1 = resp1.json()
            assert len(body1["items"]) == 10
            assert body1["page_info"]["has_next_page"] is True
            next_cursor = body1["page_info"]["next_cursor"]
            assert next_cursor is not None

            # Page 2: Keyset cursor navigation
            t1 = time.perf_counter()
            resp2 = await client.get(
                f"/api/v1/search?q={search_query}&limit=10&cursor={next_cursor}",
                headers=env["admin_headers"],
            )
            latency_ms_2 = (time.perf_counter() - t1) * 1000
            assert resp2.status_code == 200, f"Search page 2 failed: {resp2.text}"
            assert latency_ms_2 < 100.0, f"Search page 2 exceeded 100ms SLA: {latency_ms_2:.2f}ms"

            body2 = resp2.json()
            assert len(body2["items"]) == 10

            # Verify no overlapping table IDs across consecutive pages
            page1_ids = {item["id"] for item in body1["items"]}
            page2_ids = {item["id"] for item in body2["items"]}
            assert page1_ids.isdisjoint(page2_ids), "Keyset pagination produced duplicate items across pages!"
    finally:
        app.dependency_overrides.clear()


# ==============================================================================
# Quickstart Scenario 3: SQL-Level RBAC Enforcement & DENY Precedence
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_3_sql_level_rbac_enforcement_and_deny_precedence(
    test_db_session: AsyncSession, e2e_test_environment: dict
):
    """
    Scenario 3 Verification:
    1. AnalystA is granted VIEW_METADATA on Database_Analytics schema.
    2. Add an explicit DENY rule on table Database_Analytics.public.salary_records.
    3. Issue search query /api/v1/search?q=salary under AnalystA JWT token.
    4. Confirm zero rows from salary_records appear in search results.
    5. Direct GET /api/v1/tables/{salary_table_id} under AnalystA returns HTTP 403 Forbidden.
    6. Direct GET /api/v1/tables/{allowed_table_id} under AnalystA returns HTTP 200 OK.
    """
    env = e2e_test_environment
    suffix = env["suffix"]
    analyst_role = env["analyst_role"]

    crypto = EnvelopeEncryptionService()
    enc_dek, enc_creds = crypto.encrypt_credentials({"username": "u", "password": "p"})
    conn = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"analytics_dw_rbac_{suffix}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host="localhost",
        port=5432,
        database_name=f"Database_Analytics_{suffix}",
        owner=env["admin_user"].username,
        encrypted_dek=enc_dek,
        encrypted_credentials=enc_creds,
        is_active=True,
    )
    test_db_session.add(conn)

    schema = DatabaseSchema(
        id=uuid.uuid4(),
        connection_id=conn.id,
        name=f"public_rbac_{suffix}",
        status=LifecycleStatus.ACTIVE,
    )
    test_db_session.add(schema)
    await test_db_session.flush()

    # Table 1: Standard allowed table
    allowed_table = CatalogTable(
        id=uuid.uuid4(),
        schema_id=schema.id,
        name=f"dim_customer_orders_{suffix}",
        table_type=TableType.TABLE,
        source_description="Customer order analytics dimension",
        owner="analytics_team",
        status=LifecycleStatus.ACTIVE,
    )
    test_db_session.add(allowed_table)

    # Table 2: Restricted salary table
    denied_salary_table = CatalogTable(
        id=uuid.uuid4(),
        schema_id=schema.id,
        name=f"salary_records_{suffix}",
        table_type=TableType.TABLE,
        source_description="Confidential executive payroll and salary records",
        owner="hr_executive",
        status=LifecycleStatus.ACTIVE,
    )
    test_db_session.add(denied_salary_table)
    await test_db_session.flush()

    # Rule 1: ALLOW VIEW_METADATA on the Schema for Analyst role
    allow_rule = PermissionRule(
        id=uuid.uuid4(),
        role_id=analyst_role.id,
        resource_type=ResourceType.SCHEMA,
        resource_id=schema.id,
        action=PermissionAction.VIEW_METADATA,
        effect=PermissionEffect.ALLOW,
    )
    test_db_session.add(allow_rule)

    # Rule 2: Explicit DENY VIEW_METADATA on salary_records for Analyst role
    deny_rule = PermissionRule(
        id=uuid.uuid4(),
        role_id=analyst_role.id,
        resource_type=ResourceType.TABLE,
        resource_id=denied_salary_table.id,
        action=PermissionAction.VIEW_METADATA,
        effect=PermissionEffect.DENY,
    )
    test_db_session.add(deny_rule)
    await test_db_session.flush()

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Search under Analyst token: salary_records must be completely omitted
            search_resp = await client.get(
                f"/api/v1/search?q=salary_records_{suffix}",
                headers=env["analyst_headers"],
            )
            assert search_resp.status_code == 200
            search_items = search_resp.json()["items"]
            assert len(search_items) == 0, "Denied salary_records leaked through SQL-level RBAC search filter!"

            # 2. Attempt direct GET /api/v1/tables/{salary_table_id} -> HTTP 403 Forbidden
            direct_denied = await client.get(
                f"/api/v1/tables/{denied_salary_table.id}",
                headers=env["analyst_headers"],
            )
            assert direct_denied.status_code == 403, (
                f"Expected 403 Forbidden on denied table, got {direct_denied.status_code}: {direct_denied.text}"
            )

            # 3. Direct GET /api/v1/tables/{allowed_table_id} -> HTTP 200 OK
            direct_allowed = await client.get(
                f"/api/v1/tables/{allowed_table.id}",
                headers=env["analyst_headers"],
            )
            assert direct_allowed.status_code == 200
            allowed_data = direct_allowed.json()
            assert allowed_data["id"] == str(allowed_table.id)
            assert allowed_data["name"] == allowed_table.name
    finally:
        app.dependency_overrides.clear()


# ==============================================================================
# Quickstart Scenario 4: Bulk Ingestion Preserving Descriptions + Version Diffing
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_4_bulk_ingestion_preserves_curation_and_version_diffing(
    test_db_session: AsyncSession, e2e_test_environment: dict
):
    """
    Scenario 4 Verification:
    1. Bulk ingest schema with table 'orders' (v1 snapshot created).
    2. Data steward curates approved_description via PATCH /api/v1/tables/{id}/description.
    3. Re-ingest with modified columns (add 'discount', remove 'status', change 'amount' type)
       and new source_description.
    4. Confirm approved_description is preserved and prioritized over source_description.
    5. Query GET /api/v1/tables/{id}/versions/compare?v1=1&v2=2 and assert exact structural diff.
    """
    env = e2e_test_environment
    suffix = env["suffix"]

    crypto = EnvelopeEncryptionService()
    enc_dek, enc_creds = crypto.encrypt_credentials({"username": "u", "password": "p"})
    conn = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"ingest_conn_{suffix}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host="localhost",
        port=5432,
        database_name=f"ingest_db_{suffix}",
        owner=env["admin_user"].username,
        encrypted_dek=enc_dek,
        encrypted_credentials=enc_creds,
        is_active=True,
    )
    test_db_session.add(conn)
    await test_db_session.flush()

    schema_name = f"ecommerce_{suffix}"

    # Batch 1: Initial Ingestion
    batch_1 = BulkIngestionRequest(
        connection_id=conn.id,
        schema_name=schema_name,
        schema_description="E-commerce primary transactions schema",
        tables=[
            IngestTablePayload(
                name="orders",
                table_type=TableType.TABLE,
                row_count_estimate=10000,
                size_bytes=1048576,
                source_description="Raw transactional database orders",
                owner="sales_team",
                columns=[
                    IngestColumnPayload(
                        name="order_id",
                        ordinal_position=1,
                        data_type="BIGINT",
                        raw_data_type="bigint",
                        is_nullable=False,
                        is_primary_key=True,
                        source_description="Order surrogate key",
                        classification_level=ClassificationLevel.INTERNAL,
                    ),
                    IngestColumnPayload(
                        name="amount",
                        ordinal_position=2,
                        data_type="INTEGER",
                        raw_data_type="int",
                        is_nullable=False,
                        is_primary_key=False,
                        source_description="Order total in cents",
                        classification_level=ClassificationLevel.INTERNAL,
                    ),
                    IngestColumnPayload(
                        name="status",
                        ordinal_position=3,
                        data_type="VARCHAR(32)",
                        raw_data_type="varchar(32)",
                        is_nullable=False,
                        is_primary_key=False,
                        source_description="Order fulfillment status",
                        classification_level=ClassificationLevel.INTERNAL,
                    ),
                ],
            )
        ],
    )

    ingest_res_1 = await bulk_ingestion_service.ingest_bulk_metadata(
        session=test_db_session,
        payload=batch_1,
        actor_id=env["admin_user"].id,
    )
    assert ingest_res_1.success is True

    # Find created orders table ID
    tbl_stmt = select(CatalogTable).where(
        CatalogTable.schema_id == ingest_res_1.schema_id,
        CatalogTable.name == "orders",
    )
    tbl_res = await test_db_session.execute(tbl_stmt)
    orders_table = tbl_res.scalar_one()
    orders_table_id = orders_table.id

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 2. Steward curates approved_description
            curated_desc = "Curated Master Orders Catalog Entity - Gold Tier"
            patch_resp = await client.patch(
                f"/api/v1/tables/{orders_table_id}/description",
                json={"approved_description": curated_desc},
                headers=env["admin_headers"],
            )
            assert patch_resp.status_code == 200, f"Patch failed: {patch_resp.text}"

            # 3. Batch 2: Re-ingest with structural changes: add 'discount', remove 'status', modify 'amount'
            batch_2 = BulkIngestionRequest(
                connection_id=conn.id,
                schema_name=schema_name,
                schema_description="E-commerce primary transactions schema",
                tables=[
                    IngestTablePayload(
                        name="orders",
                        table_type=TableType.TABLE,
                        row_count_estimate=12500,
                        size_bytes=1310720,
                        source_description="Updated automated database description from CDC",
                        owner="sales_team",
                        columns=[
                            IngestColumnPayload(
                                name="order_id",
                                ordinal_position=1,
                                data_type="BIGINT",
                                raw_data_type="bigint",
                                is_nullable=False,
                                is_primary_key=True,
                                source_description="Order surrogate key",
                                classification_level=ClassificationLevel.INTERNAL,
                            ),
                            IngestColumnPayload(
                                name="amount",
                                ordinal_position=2,
                                data_type="NUMERIC(12,2)",  # MODIFIED data type
                                raw_data_type="numeric(12,2)",
                                is_nullable=False,
                                is_primary_key=False,
                                source_description="Order total in decimal currency",
                                classification_level=ClassificationLevel.INTERNAL,
                            ),
                            IngestColumnPayload(
                                name="discount",  # ADDED column
                                ordinal_position=3,
                                data_type="NUMERIC(5,2)",
                                raw_data_type="numeric(5,2)",
                                is_nullable=True,
                                is_primary_key=False,
                                source_description="Applied order discount",
                                classification_level=ClassificationLevel.INTERNAL,
                            ),
                            # status column REMOVED
                        ],
                    )
                ],
            )

            ingest_res_2 = await bulk_ingestion_service.ingest_bulk_metadata(
                session=test_db_session,
                payload=batch_2,
                actor_id=env["admin_user"].id,
            )
            assert ingest_res_2.success is True

            # 4. Verify approved_description is preserved
            orders_db = await test_db_session.get(CatalogTable, orders_table_id)
            assert orders_db.approved_description == curated_desc
            assert orders_db.source_description == "Updated automated database description from CDC"
            assert orders_db.effective_description is not None

            # 5. Query version diff API: compare v1 and v2
            diff_resp = await client.get(
                f"/api/v1/tables/{orders_table_id}/versions/compare?v1=1&v2=2",
                headers=env["admin_headers"],
            )
            assert diff_resp.status_code == 200, f"Diff API failed: {diff_resp.text}"
            diff_data = diff_resp.json()

            assert diff_data["v1_num"] == 1
            assert diff_data["v2_num"] == 2

            added_names = [c["name"] for c in diff_data["added_columns"]]
            assert "discount" in added_names

            removed_names = [c["name"] for c in diff_data["removed_columns"]]
            assert "status" in removed_names

            modified_names = [c["column_name"] for c in diff_data["modified_columns"]]
            assert "amount" in modified_names
    finally:
        app.dependency_overrides.clear()


# ==============================================================================
# Quickstart Scenario 5: Partitioned Immutable Audit Logs + Concurrent Reports
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_5_immutable_audit_logs_and_concurrent_reports(
    test_db_session: AsyncSession, e2e_test_environment: dict
):
    """
    Scenario 5 Verification:
    1. Query audit_logs table for audit events.
    2. Confirm immutability rule: direct SQL UPDATE on audit_logs affects 0 rows.
    3. Confirm immutability rule: direct SQL DELETE on audit_logs affects 0 rows.
    4. Execute concurrent refresh of all PostgreSQL materialized views.
    5. Query /api/v1/reports/completeness and /api/v1/reports/classification in < 50ms.
    """
    env = e2e_test_environment

    # Refresh materialized views concurrently
    await report_service.refresh_all_materialized_views_concurrently()

    # 1. Audit Log Immutability Verification
    # Insert an isolated audit log in test transaction
    event_id = uuid.uuid4()
    await test_db_session.execute(
        text("""
            INSERT INTO audit_logs (id, created_at, actor_username, action, entity_type, entity_id, diff_jsonb)
            VALUES (:id, NOW(), :actor, 'VERIFY_IMMUTABILITY', 'TABLE', :entity_id, '{"status": "ok"}'::jsonb);
        """),
        {
            "id": event_id,
            "actor": env["admin_user"].username,
            "entity_id": uuid.uuid4(),
        },
    )
    await test_db_session.flush()

    # Attempt direct SQL UPDATE: rule audit_logs_no_update must prevent mutation
    update_res = await test_db_session.execute(
        text("UPDATE audit_logs SET actor_username = 'hacker_malicious' WHERE id = :id"),
        {"id": event_id},
    )
    assert update_res.rowcount in (0, -1), "PostgreSQL immutability rule failed to prevent audit log update!"

    # Attempt direct SQL DELETE: rule audit_logs_no_delete must prevent deletion
    delete_res = await test_db_session.execute(
        text("DELETE FROM audit_logs WHERE id = :id"),
        {"id": event_id},
    )
    assert delete_res.rowcount in (0, -1), "PostgreSQL immutability rule failed to prevent audit log deletion!"

    # Verify record still has original actor_username
    log_check = await test_db_session.execute(
        text("SELECT actor_username FROM audit_logs WHERE id = :id"),
        {"id": event_id},
    )
    assert log_check.scalar_one() == env["admin_user"].username

    # 2. Query Materialized View Reports via API
    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Completeness report
            t_comp = time.perf_counter()
            comp_resp = await client.get("/api/v1/reports/completeness", headers=env["admin_headers"])
            comp_latency_ms = (time.perf_counter() - t_comp) * 1000
            assert comp_resp.status_code == 200, f"Completeness report failed: {comp_resp.text}"
            assert comp_latency_ms < 50.0, f"Completeness report exceeded 50ms: {comp_latency_ms:.2f}ms"
            comp_data = comp_resp.json()
            assert "overall_completeness_pct" in comp_data
            assert "total_databases" in comp_data
            assert "database_breakdown" in comp_data

            # Classification report
            t_class = time.perf_counter()
            class_resp = await client.get("/api/v1/reports/classification", headers=env["admin_headers"])
            class_latency_ms = (time.perf_counter() - t_class) * 1000
            assert class_resp.status_code == 200, f"Classification report failed: {class_resp.text}"
            assert class_latency_ms < 50.0, f"Classification report exceeded 50ms: {class_latency_ms:.2f}ms"
            class_data = class_resp.json()
            assert "total_classified_columns" in class_data
            assert "breakdown" in class_data
    finally:
        app.dependency_overrides.clear()
