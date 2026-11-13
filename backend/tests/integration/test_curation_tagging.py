from __future__ import annotations

import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.database import get_db_session
from backend.src.main import app
from backend.src.models.audit import AuditLog
from backend.src.models.catalog import (
    CatalogColumn,
    CatalogTable,
    ClassificationLevel,
    DatabaseSchema,
    LifecycleStatus,
    TableType,
    Tag,
    TagAssignment,
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
from backend.src.models.versioning import SchemaChangeLog, SchemaChangeType
from backend.src.services.auth.jwt_service import auth_service


@pytest.fixture
async def curation_test_data(test_db_session: AsyncSession):
    """Sets up a catalog hierarchy, tags, steward user (with permissions), and viewer user (read-only)."""
    suffix = uuid.uuid4().hex[:8]

    # Roles
    steward_role = Role(
        id=uuid.uuid4(),
        name=f"StewardRole_{suffix}",
        description="Data Steward with curation and tagging permissions",
        is_system_role=False,
    )
    viewer_role = Role(
        id=uuid.uuid4(),
        name=f"ViewerRole_{suffix}",
        description="Viewer role with read-only metadata permission",
        is_system_role=False,
    )
    test_db_session.add_all([steward_role, viewer_role])
    await test_db_session.flush()

    # Permissions for Steward: EDIT_DESCRIPTION, MANAGE_TAGS, VIEW_METADATA
    perms = [
        PermissionRule(
            id=uuid.uuid4(),
            role_id=steward_role.id,
            resource_type=ResourceType.GLOBAL,
            resource_id=None,
            action=PermissionAction.VIEW_METADATA,
            effect=PermissionEffect.ALLOW,
        ),
        PermissionRule(
            id=uuid.uuid4(),
            role_id=steward_role.id,
            resource_type=ResourceType.GLOBAL,
            resource_id=None,
            action=PermissionAction.EDIT_DESCRIPTION,
            effect=PermissionEffect.ALLOW,
        ),
        PermissionRule(
            id=uuid.uuid4(),
            role_id=steward_role.id,
            resource_type=ResourceType.GLOBAL,
            resource_id=None,
            action=PermissionAction.MANAGE_TAGS,
            effect=PermissionEffect.ALLOW,
        ),
        PermissionRule(
            id=uuid.uuid4(),
            role_id=viewer_role.id,
            resource_type=ResourceType.GLOBAL,
            resource_id=None,
            action=PermissionAction.VIEW_METADATA,
            effect=PermissionEffect.ALLOW,
        ),
    ]
    test_db_session.add_all(perms)
    await test_db_session.flush()

    # Users
    steward = User(
        id=uuid.uuid4(),
        username=f"steward_{suffix}",
        email=f"steward_{suffix}@company.internal",
        hashed_password=auth_service.hash_password("Pass1234#"),
        full_name="Steward User",
        status=UserStatus.ACTIVE,
        is_superadmin=False,
    )
    viewer = User(
        id=uuid.uuid4(),
        username=f"viewer_{suffix}",
        email=f"viewer_{suffix}@company.internal",
        hashed_password=auth_service.hash_password("Pass1234#"),
        full_name="Viewer User",
        status=UserStatus.ACTIVE,
        is_superadmin=False,
    )
    test_db_session.add_all([steward, viewer])
    await test_db_session.flush()

    # User Roles
    test_db_session.add_all([
        UserRole(user_id=steward.id, role_id=steward_role.id),
        UserRole(user_id=viewer.id, role_id=viewer_role.id),
    ])
    await test_db_session.flush()

    # Database Connection & Schema
    conn = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"conn_curation_{suffix}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host="localhost",
        port=5432,
        database_name=f"db_{suffix}",
        owner=steward.username,
        kek_version=1,
        encrypted_dek=b"dummy_dek_32_bytes_data_key_001",
        encrypted_credentials=b"dummy_creds_ciphertext_data_001",
    )
    test_db_session.add(conn)
    await test_db_session.flush()

    schema = DatabaseSchema(
        id=uuid.uuid4(),
        connection_id=conn.id,
        name=f"sales_{suffix}",
        owner=steward.username,
    )
    test_db_session.add(schema)
    await test_db_session.flush()

    # Table
    table = CatalogTable(
        id=uuid.uuid4(),
        schema_id=schema.id,
        name=f"orders_{suffix}",
        table_type=TableType.TABLE,
        row_count_estimate=12000,
        size_bytes=2048576,
        source_description="Raw ingested orders table",
        approved_description=None,
        owner=steward.username,
        status=LifecycleStatus.ACTIVE,
    )
    test_db_session.add(table)
    await test_db_session.flush()

    # Column
    column = CatalogColumn(
        id=uuid.uuid4(),
        table_id=table.id,
        name="customer_ssn",
        ordinal_position=1,
        data_type="VARCHAR(11)",
        raw_data_type="varchar",
        is_nullable=False,
        is_primary_key=False,
        is_foreign_key=False,
        source_description="Customer SSN from upstream ingestion",
        approved_description=None,
        classification_level=ClassificationLevel.CONFIDENTIAL,
        status=LifecycleStatus.ACTIVE,
    )
    test_db_session.add(column)

    # Tags
    tag_pii = Tag(
        id=uuid.uuid4(),
        name=f"PII_{suffix}",
        color="#EF4444",
        description="Personally Identifiable Information",
        is_governed=True,
    )
    tag_finance = Tag(
        id=uuid.uuid4(),
        name=f"Finance_{suffix}",
        color="#10B981",
        description="Financial and Revenue Data",
        is_governed=False,
    )
    test_db_session.add_all([tag_pii, tag_finance])
    await test_db_session.flush()

    # JWT Tokens
    steward_token = auth_service.create_access_token(
        data={
            "sub": str(steward.id),
            "username": steward.username,
            "email": steward.email,
            "roles": [steward_role.name],
            "is_superadmin": False,
        }
    )
    viewer_token = auth_service.create_access_token(
        data={
            "sub": str(viewer.id),
            "username": viewer.username,
            "email": viewer.email,
            "roles": [viewer_role.name],
            "is_superadmin": False,
        }
    )

    return {
        "steward": steward,
        "viewer": viewer,
        "steward_headers": {"Authorization": f"Bearer {steward_token}"},
        "viewer_headers": {"Authorization": f"Bearer {viewer_token}"},
        "table": table,
        "column": column,
        "tag_pii": tag_pii,
        "tag_finance": tag_finance,
    }


@pytest.mark.asyncio
async def test_inline_table_description_update(
    test_db_session: AsyncSession,
    curation_test_data: dict,
):
    """
    T045: Assert PATCH /api/v1/tables/{table_id}/description updates approved_description,
    records DESCRIPTION_UPDATED in schema_change_logs, and persists an AuditLog entry.
    """
    table = curation_test_data["table"]
    steward = curation_test_data["steward"]

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            new_desc = "Curated customer purchase orders with compliance validation."
            resp = await client.patch(
                f"/api/v1/tables/{table.id}/description",
                json={"approved_description": new_desc},
                headers=curation_test_data["steward_headers"],
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["id"] == str(table.id)
            assert data["approved_description"] == new_desc

            # 1. Verify DB update on CatalogTable
            stmt = select(CatalogTable).where(CatalogTable.id == table.id)
            result = await test_db_session.execute(stmt)
            updated_tbl = result.scalar_one()
            assert updated_tbl.approved_description == new_desc

            # 2. Verify SchemaChangeLog record
            change_stmt = select(SchemaChangeLog).where(
                SchemaChangeLog.table_id == table.id,
                SchemaChangeLog.change_type == SchemaChangeType.DESCRIPTION_UPDATED,
            )
            change_result = await test_db_session.execute(change_stmt)
            change_log = change_result.scalar_one_or_none()
            assert change_log is not None
            assert change_log.column_name is None
            assert change_log.new_value == {"approved_description": new_desc}
            assert change_log.actor_id == steward.id

            # 3. Verify immutable AuditLog record
            audit_stmt = select(AuditLog).where(
                AuditLog.entity_type == EntityType.TABLE,
                AuditLog.entity_id == table.id,
                AuditLog.action == "EDIT_DESCRIPTION",
            )
            audit_result = await test_db_session.execute(audit_stmt)
            audit_entry = audit_result.scalar_one_or_none()
            assert audit_entry is not None
            assert audit_entry.actor_id == steward.id
            assert audit_entry.actor_username == steward.username
            assert audit_entry.diff_jsonb.get("new_value") == new_desc
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_inline_column_description_update(
    test_db_session: AsyncSession,
    curation_test_data: dict,
):
    """
    T045: Assert PATCH /api/v1/columns/{column_id}/description updates column approved_description,
    records DESCRIPTION_UPDATED in schema_change_logs with column_name, and creates an AuditLog entry.
    """
    column = curation_test_data["column"]
    table = curation_test_data["table"]
    steward = curation_test_data["steward"]

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            new_desc = "Standardized 9-digit SSN formatted as AAA-GG-SSSS."
            resp = await client.patch(
                f"/api/v1/columns/{column.id}/description",
                json={"approved_description": new_desc},
                headers=curation_test_data["steward_headers"],
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["id"] == str(column.id)
            assert data["approved_description"] == new_desc

            # 1. Verify DB update on CatalogColumn
            col_stmt = select(CatalogColumn).where(CatalogColumn.id == column.id)
            col_res = await test_db_session.execute(col_stmt)
            updated_col = col_res.scalar_one()
            assert updated_col.approved_description == new_desc

            # 2. Verify SchemaChangeLog record
            change_stmt = select(SchemaChangeLog).where(
                SchemaChangeLog.table_id == table.id,
                SchemaChangeLog.column_name == column.name,
                SchemaChangeLog.change_type == SchemaChangeType.DESCRIPTION_UPDATED,
            )
            change_res = await test_db_session.execute(change_stmt)
            change_log = change_res.scalar_one_or_none()
            assert change_log is not None
            assert change_log.new_value == {"approved_description": new_desc}
            assert change_log.actor_id == steward.id

            # 3. Verify immutable AuditLog record
            audit_stmt = select(AuditLog).where(
                AuditLog.entity_type == EntityType.COLUMN,
                AuditLog.entity_id == column.id,
                AuditLog.action == "EDIT_DESCRIPTION",
            )
            audit_res = await test_db_session.execute(audit_stmt)
            audit_entry = audit_res.scalar_one_or_none()
            assert audit_entry is not None
            assert audit_entry.actor_id == steward.id
            assert audit_entry.diff_jsonb.get("new_value") == new_desc
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_bulk_tag_assignment_and_removal_atomic(
    test_db_session: AsyncSession,
    curation_test_data: dict,
):
    """
    T045: Assert POST /api/v1/tags/bulk-assign atomically assigns and removes tags across
    multiple targets (table & column) and creates MANAGE_TAGS AuditLog records.
    """
    table = curation_test_data["table"]
    column = curation_test_data["column"]
    tag_pii = curation_test_data["tag_pii"]
    tag_finance = curation_test_data["tag_finance"]
    steward = curation_test_data["steward"]

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Assign tags: tag_pii to table & column, tag_finance to table
            assign_payload = {
                "tag_ids": [str(tag_pii.id), str(tag_finance.id)],
                "action": "ASSIGN",
                "targets": [
                    {"entity_type": "TABLE", "entity_id": str(table.id)},
                    {"entity_type": "COLUMN", "entity_id": str(column.id)},
                ],
            }
            resp = await client.post(
                "/api/v1/tags/bulk-assign",
                json=assign_payload,
                headers=curation_test_data["steward_headers"],
            )
            assert resp.status_code == 200, resp.text
            res_data = resp.json()
            assert res_data["success"] is True
            assert res_data["affected_count"] == 4

            # Verify DB assignments
            assigned_stmt = select(TagAssignment).where(
                TagAssignment.tag_id.in_([tag_pii.id, tag_finance.id])
            )
            assigned_res = await test_db_session.execute(assigned_stmt)
            assignments = assigned_res.scalars().all()
            assert len(assignments) == 4

            # Verify AuditLog entries
            audit_stmt = select(AuditLog).where(
                AuditLog.action == "MANAGE_TAGS",
                AuditLog.actor_id == steward.id,
            )
            audit_res = await test_db_session.execute(audit_stmt)
            audit_entries = audit_res.scalars().all()
            assert len(audit_entries) >= 4

            # 2. Remove tag_pii from table
            remove_payload = {
                "tag_ids": [str(tag_pii.id)],
                "action": "REMOVE",
                "targets": [
                    {"entity_type": "TABLE", "entity_id": str(table.id)},
                ],
            }
            resp_remove = await client.post(
                "/api/v1/tags/bulk-assign",
                json=remove_payload,
                headers=curation_test_data["steward_headers"],
            )
            assert resp_remove.status_code == 200, resp_remove.text
            assert resp_remove.json()["success"] is True
            assert resp_remove.json()["affected_count"] == 1

            # Verify table no longer has tag_pii
            check_stmt = select(TagAssignment).where(
                TagAssignment.tag_id == tag_pii.id,
                TagAssignment.entity_type == EntityType.TABLE,
                TagAssignment.entity_id == table.id,
            )
            check_res = await test_db_session.execute(check_stmt)
            assert check_res.scalar_one_or_none() is None

            # Verify column still has tag_pii
            col_check_stmt = select(TagAssignment).where(
                TagAssignment.tag_id == tag_pii.id,
                TagAssignment.entity_type == EntityType.COLUMN,
                TagAssignment.entity_id == column.id,
            )
            col_check_res = await test_db_session.execute(col_check_stmt)
            assert col_check_res.scalar_one_or_none() is not None
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_curation_tagging_rbac_enforcement(
    test_db_session: AsyncSession,
    curation_test_data: dict,
):
    """
    T045: Assert that unprivileged users without EDIT_DESCRIPTION or MANAGE_TAGS permissions
    receive 403 Forbidden.
    """
    table = curation_test_data["table"]
    column = curation_test_data["column"]
    tag_pii = curation_test_data["tag_pii"]

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Attempt to edit table description without EDIT_DESCRIPTION
            resp1 = await client.patch(
                f"/api/v1/tables/{table.id}/description",
                json={"approved_description": "Unauthorized table update"},
                headers=curation_test_data["viewer_headers"],
            )
            assert resp1.status_code == 403
            assert "Forbidden" in resp1.json()["detail"]

            # 2. Attempt to edit column description without EDIT_DESCRIPTION
            resp2 = await client.patch(
                f"/api/v1/columns/{column.id}/description",
                json={"approved_description": "Unauthorized column update"},
                headers=curation_test_data["viewer_headers"],
            )
            assert resp2.status_code == 403
            assert "Forbidden" in resp2.json()["detail"]

            # 3. Attempt bulk tagging without MANAGE_TAGS
            resp3 = await client.post(
                "/api/v1/tags/bulk-assign",
                json={
                    "tag_ids": [str(tag_pii.id)],
                    "action": "ASSIGN",
                    "targets": [{"entity_type": "TABLE", "entity_id": str(table.id)}],
                },
                headers=curation_test_data["viewer_headers"],
            )
            assert resp3.status_code == 403
            assert "Forbidden" in resp3.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_db_session, None)

