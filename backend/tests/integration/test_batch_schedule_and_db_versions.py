from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.database import get_db_session
from backend.src.main import app
from backend.src.models.audit import AuditLog
from backend.src.models.catalog import CatalogTable, DatabaseSchema, LifecycleStatus, TableType
from backend.src.models.connections import DatabaseConnection, DatabaseEngine, SyncSchedule
from backend.src.models.identity import User, UserStatus
from backend.src.models.rbac import (
    EntityType,
    PermissionAction,
    PermissionEffect,
    PermissionRule,
    ResourceType,
)
from backend.src.models.sync_jobs import SyncJob
from backend.src.models.versioning import TableVersion
from backend.src.services.auth.jwt_service import auth_service


def _make_auth_headers(user: User) -> dict:
    token = auth_service.create_access_token(
        data={
            "sub": str(user.id),
            "username": user.username,
            "email": user.email,
            "roles": ["SuperAdmin"] if user.is_superadmin else [],
            "is_superadmin": user.is_superadmin,
        }
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_batch_schedule_persists_audit_log_with_non_null_entity_id(
    test_db_session: AsyncSession,
):
    """
    Verifies POST /api/v1/connections/batch-schedule creates/updates SyncSchedule
    records and persists an AuditLog with a non-null entity_id (found_ids[0]).
    """
    suffix = uuid.uuid4().hex[:8]
    admin = User(
        id=uuid.uuid4(),
        username=f"batch_admin_{suffix}",
        email=f"batch_admin_{suffix}@company.internal",
        hashed_password=auth_service.hash_password("Pass123!"),
        full_name="Batch Admin",
        status=UserStatus.ACTIVE,
        is_superadmin=True,
    )
    test_db_session.add(admin)

    conn1 = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"batch_conn1_{suffix}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host="10.0.0.1",
        port=5432,
        database_name=f"db1_{suffix}",
        owner=admin.username,
        kek_version=1,
        encrypted_dek=b"dek_1",
        encrypted_credentials=b"cred_1",
    )
    conn2 = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"batch_conn2_{suffix}",
        engine_type=DatabaseEngine.MYSQL,
        host="10.0.0.2",
        port=3306,
        database_name=f"db2_{suffix}",
        owner=admin.username,
        kek_version=1,
        encrypted_dek=b"dek_2",
        encrypted_credentials=b"cred_2",
    )
    test_db_session.add_all([conn1, conn2])
    await test_db_session.flush()

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/connections/batch-schedule",
                headers=_make_auth_headers(admin),
                json={
                    "connection_ids": [str(conn1.id), str(conn2.id)],
                    "cron_expression": "0 2 * * *",
                    "is_enabled": True,
                },
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["updated_count"] == 2
            assert data["cron_expression"] == "0 2 * * *"

            # Verify SyncSchedule rows
            scheds = (
                await test_db_session.execute(
                    select(SyncSchedule).where(
                        SyncSchedule.connection_id.in_([conn1.id, conn2.id])
                    )
                )
            ).scalars().all()
            assert len(scheds) == 2

            # Verify AuditLog persistence with non-null entity_id
            audit_logs = (
                await test_db_session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "BATCH_UPDATE_SYNC_SCHEDULE",
                        AuditLog.actor_username == admin.username,
                    )
                )
            ).scalars().all()
            assert len(audit_logs) == 1
            assert audit_logs[0].entity_type == EntityType.DATABASE
            assert audit_logs[0].entity_id in {conn1.id, conn2.id}
            assert audit_logs[0].diff_jsonb["updated_count"] == 2
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_connection_versions_compare_added_dropped_modified_and_rbac(
    test_db_session: AsyncSession,
):
    """
    Verifies GET /api/v1/connections/{connection_id}/versions and
    GET /api/v1/connections/{connection_id}/versions/compare:
    - Detects ADDED, DROPPED, and MODIFIED tables with accurate column deltas.
    - Enforces RBAC VIEW_METADATA filtering for non-superadmin users.
    """
    suffix = uuid.uuid4().hex[:8]
    superadmin = User(
        id=uuid.uuid4(),
        username=f"ver_admin_{suffix}",
        email=f"ver_admin_{suffix}@company.internal",
        hashed_password=auth_service.hash_password("Pass123!"),
        full_name="Version Admin",
        status=UserStatus.ACTIVE,
        is_superadmin=True,
    )
    limited_user = User(
        id=uuid.uuid4(),
        username=f"ver_limited_{suffix}",
        email=f"ver_limited_{suffix}@company.internal",
        hashed_password=auth_service.hash_password("Pass123!"),
        full_name="Limited User",
        status=UserStatus.ACTIVE,
        is_superadmin=False,
    )
    test_db_session.add_all([superadmin, limited_user])

    conn = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"ver_conn_{suffix}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host="10.0.0.5",
        port=5432,
        database_name=f"ver_db_{suffix}",
        owner=superadmin.username,
        kek_version=1,
        encrypted_dek=b"dek_v",
        encrypted_credentials=b"cred_v",
    )
    test_db_session.add(conn)
    await test_db_session.flush()

    schema = DatabaseSchema(
        id=uuid.uuid4(),
        connection_id=conn.id,
        name="public",
        owner=superadmin.username,
    )
    test_db_session.add(schema)
    await test_db_session.flush()

    t1 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 11, 2, 10, 0, 0, tzinfo=timezone.utc)

    # 1. Modified table (authorized for limited_user)
    tbl_mod = CatalogTable(
        id=uuid.uuid4(),
        schema_id=schema.id,
        name="orders",
        table_type=TableType.TABLE,
        status=LifecycleStatus.ACTIVE,
    )
    # 2. Added table (NOT authorized for limited_user)
    tbl_add = CatalogTable(
        id=uuid.uuid4(),
        schema_id=schema.id,
        name="invoices",
        table_type=TableType.TABLE,
        status=LifecycleStatus.ACTIVE,
    )
    # 3. Dropped table (authorized for limited_user)
    tbl_drop = CatalogTable(
        id=uuid.uuid4(),
        schema_id=schema.id,
        name="legacy_logs",
        table_type=TableType.TABLE,
        status=LifecycleStatus.DELETED,
    )
    test_db_session.add_all([tbl_mod, tbl_add, tbl_drop])
    await test_db_session.flush()

    # Grant limited_user VIEW_METADATA only on tbl_mod and tbl_drop (not tbl_add)
    test_db_session.add_all(
        [
            PermissionRule(
                id=uuid.uuid4(),
                user_id=limited_user.id,
                resource_type=ResourceType.TABLE,
                resource_id=tbl_mod.id,
                action=PermissionAction.VIEW_METADATA,
                effect=PermissionEffect.ALLOW,
            ),
            PermissionRule(
                id=uuid.uuid4(),
                user_id=limited_user.id,
                resource_type=ResourceType.TABLE,
                resource_id=tbl_drop.id,
                action=PermissionAction.VIEW_METADATA,
                effect=PermissionEffect.ALLOW,
            ),
        ]
    )

    # SyncJobs at t1 and t2
    test_db_session.add_all(
        [
            SyncJob(
                id=uuid.uuid4(),
                connection_id=conn.id,
                status="SUCCESS",
                triggered_by="MANUAL",
                started_at=t1 - timedelta(seconds=10),
                completed_at=t1,
                tables_scanned=2,
                changes_detected=0,
            ),
            SyncJob(
                id=uuid.uuid4(),
                connection_id=conn.id,
                status="SUCCESS",
                triggered_by="SCHEDULE",
                started_at=t2 - timedelta(seconds=10),
                completed_at=t2,
                tables_scanned=2,
                changes_detected=3,
            ),
        ]
    )

    # Snapshots
    test_db_session.add_all(
        [
            TableVersion(
                id=uuid.uuid4(),
                table_id=tbl_mod.id,
                version_num=1,
                created_at=t1,
                snapshot_jsonb={
                    "name": "orders",
                    "status": "ACTIVE",
                    "columns": [
                        {"name": "id", "data_type": "INT", "is_nullable": False},
                        {"name": "amount", "data_type": "INT", "is_nullable": True},
                    ],
                },
            ),
            TableVersion(
                id=uuid.uuid4(),
                table_id=tbl_mod.id,
                version_num=2,
                created_at=t2,
                snapshot_jsonb={
                    "name": "orders",
                    "status": "ACTIVE",
                    "columns": [
                        {"name": "id", "data_type": "INT", "is_nullable": False},
                        {"name": "amount", "data_type": "NUMERIC(12,2)", "is_nullable": False},
                        {"name": "currency", "data_type": "VARCHAR(3)", "is_nullable": False},
                    ],
                },
            ),
            TableVersion(
                id=uuid.uuid4(),
                table_id=tbl_add.id,
                version_num=1,
                created_at=t2,
                snapshot_jsonb={
                    "name": "invoices",
                    "status": "ACTIVE",
                    "columns": [
                        {"name": "id", "data_type": "UUID", "is_nullable": False},
                    ],
                },
            ),
            TableVersion(
                id=uuid.uuid4(),
                table_id=tbl_drop.id,
                version_num=1,
                created_at=t1,
                snapshot_jsonb={
                    "name": "legacy_logs",
                    "status": "ACTIVE",
                    "columns": [
                        {"name": "id", "data_type": "BIGINT", "is_nullable": False},
                    ],
                },
            ),
            TableVersion(
                id=uuid.uuid4(),
                table_id=tbl_drop.id,
                version_num=2,
                created_at=t2,
                snapshot_jsonb={
                    "name": "legacy_logs",
                    "status": "DELETED",
                    "columns": [],
                },
            ),
        ]
    )
    await test_db_session.flush()

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. List snapshot points
            snaps_resp = await client.get(
                f"/api/v1/connections/{conn.id}/versions",
                headers=_make_auth_headers(superadmin),
            )
            assert snaps_resp.status_code == 200, snaps_resp.text
            snaps = snaps_resp.json()
            assert len(snaps) == 2
            assert snaps[0]["source"] == "SYNC_JOB"

            # 2. SuperAdmin sees all 3 changed tables (1 added, 1 dropped, 1 modified)
            cmp_admin = await client.get(
                f"/api/v1/connections/{conn.id}/versions/compare",
                params={"v1_ts": t1.isoformat(), "v2_ts": t2.isoformat()},
                headers=_make_auth_headers(superadmin),
            )
            assert cmp_admin.status_code == 200, cmp_admin.text
            admin_data = cmp_admin.json()
            assert admin_data["added_tables_count"] == 1
            assert admin_data["dropped_tables_count"] == 1
            assert admin_data["modified_tables_count"] == 1
            assert admin_data["total_column_changes"] == 4  # 1 added tbl col + 1 dropped tbl col + 1 added col + 1 modified col
            by_name = {t["table_name"]: t for t in admin_data["tables"]}
            assert by_name["invoices"]["change_status"] == "ADDED"
            assert by_name["legacy_logs"]["change_status"] == "DROPPED"
            assert by_name["orders"]["change_status"] == "MODIFIED"
            assert len(by_name["orders"]["added_columns"]) == 1
            assert len(by_name["orders"]["modified_columns"]) == 1

            # 3. Limited user only sees authorized tables (orders + legacy_logs, NOT invoices)
            cmp_limited = await client.get(
                f"/api/v1/connections/{conn.id}/versions/compare",
                params={"v1_ts": t1.isoformat(), "v2_ts": t2.isoformat()},
                headers=_make_auth_headers(limited_user),
            )
            assert cmp_limited.status_code == 200, cmp_limited.text
            limited_data = cmp_limited.json()
            assert limited_data["added_tables_count"] == 0
            assert limited_data["dropped_tables_count"] == 1
            assert limited_data["modified_tables_count"] == 1
            limited_names = {t["table_name"] for t in limited_data["tables"]}
            assert limited_names == {"orders", "legacy_logs"}
    finally:
        app.dependency_overrides.clear()
