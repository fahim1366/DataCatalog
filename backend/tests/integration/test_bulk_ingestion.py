from __future__ import annotations

import uuid
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
from backend.src.models.identity import User, UserStatus
from backend.src.models.versioning import SchemaChangeLog, SchemaChangeType, TableVersion
from backend.src.schemas.ingestion import (
    BulkIngestionRequest,
    IngestColumnPayload,
    IngestTablePayload,
)
from backend.src.services.auth.jwt_service import auth_service
from backend.src.services.ingestion.bulk_ingestion_service import bulk_ingestion_service


@pytest.fixture
async def bulk_ingest_data(test_db_session: AsyncSession):
    """Sets up a database connection and an admin user for ingestion tests."""
    suffix = uuid.uuid4().hex[:8]

    # Create User
    user = User(
        id=uuid.uuid4(),
        username=f"ingest_admin_{suffix}",
        email=f"ingest_admin_{suffix}@company.internal",
        hashed_password=auth_service.hash_password("Pass1234#"),
        full_name="Ingestion Admin",
        status=UserStatus.ACTIVE,
        is_superadmin=True,
    )
    test_db_session.add(user)

    # Create Database Connection
    connection = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"prod_pg_{suffix}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host="localhost",
        port=5432,
        database_name=f"warehouse_{suffix}",
        owner=user.username,
        kek_version=1,
        encrypted_dek=b"dummy_dek_32_bytes_bulk_ingest",
        encrypted_credentials=b"dummy_credentials_bulk_ingest",
    )
    test_db_session.add(connection)
    await test_db_session.flush()

    return {
        "user": user,
        "connection": connection,
        "suffix": suffix,
    }


@pytest.mark.asyncio
async def test_bulk_ingestion_preserves_curated_descriptions_and_triggers_versioning(
    test_db_session: AsyncSession, bulk_ingest_data: dict
):
    """
    T032 Step A: Verifies that:
    1. Initial bulk ingestion creates tables, columns, version snapshots, and audit record.
    2. Human steward curation on approved_description is preserved across subsequent ingestions.
    3. Source descriptions and technical types update properly.
    4. Missing tables and columns are soft-deleted as DELETED.
    5. Structural changes trigger TableVersion increments and SchemaChangeLog entries.
    """
    connection = bulk_ingest_data["connection"]
    user = bulk_ingest_data["user"]
    schema_name = f"ecommerce_{bulk_ingest_data['suffix']}"

    # Batch 1: Initial Ingestion with 2 tables: orders and customers
    batch_1 = BulkIngestionRequest(
        connection_id=connection.id,
        schema_name=schema_name,
        schema_description="E-commerce primary schema",
        tables=[
            IngestTablePayload(
                name="orders",
                table_type=TableType.TABLE,
                row_count_estimate=10000,
                size_bytes=1048576,
                source_description="Raw orders table from database",
                owner="sales_team",
                columns=[
                    IngestColumnPayload(
                        name="order_id",
                        ordinal_position=1,
                        data_type="BIGINT",
                        raw_data_type="bigint",
                        is_nullable=False,
                        is_primary_key=True,
                        is_foreign_key=False,
                        source_description="Order ID surrogate key",
                        classification_level=ClassificationLevel.INTERNAL,
                    ),
                    IngestColumnPayload(
                        name="amount",
                        ordinal_position=2,
                        data_type="INT",
                        raw_data_type="int",
                        is_nullable=False,
                        is_primary_key=False,
                        is_foreign_key=False,
                        source_description="Order total in cents",
                        classification_level=ClassificationLevel.INTERNAL,
                    ),
                    IngestColumnPayload(
                        name="notes",
                        ordinal_position=3,
                        data_type="VARCHAR(255)",
                        raw_data_type="varchar(255)",
                        is_nullable=True,
                        is_primary_key=False,
                        is_foreign_key=False,
                        source_description="Order notes",
                        classification_level=ClassificationLevel.INTERNAL,
                    ),
                ],
            ),
            IngestTablePayload(
                name="customers",
                table_type=TableType.TABLE,
                row_count_estimate=5000,
                size_bytes=524288,
                source_description="Raw customers table",
                owner="sales_team",
                columns=[
                    IngestColumnPayload(
                        name="customer_id",
                        ordinal_position=1,
                        data_type="UUID",
                        raw_data_type="uuid",
                        is_nullable=False,
                        is_primary_key=True,
                        is_foreign_key=False,
                        source_description="Customer UUID",
                        classification_level=ClassificationLevel.INTERNAL,
                    )
                ],
            ),
        ],
    )

    # Ingest Batch 1
    resp_1 = await bulk_ingestion_service.ingest_bulk_metadata(
        session=test_db_session,
        payload=batch_1,
        actor_id=user.id,
        actor_username=user.username,
    )

    assert resp_1.success is True
    assert resp_1.tables_upserted == 2
    assert resp_1.columns_upserted == 4
    assert resp_1.versions_created == 2

    # Verify orders table exists and has Version 1
    stmt_orders = select(CatalogTable).where(
        CatalogTable.schema_id == resp_1.schema_id,
        CatalogTable.name == "orders",
    )
    orders_tbl = (await test_db_session.execute(stmt_orders)).scalar_one()
    assert orders_tbl.status == LifecycleStatus.ACTIVE
    assert orders_tbl.source_description == "Raw orders table from database"

    stmt_v1 = select(TableVersion).where(TableVersion.table_id == orders_tbl.id)
    v1_orders = (await test_db_session.execute(stmt_v1)).scalar_one()
    assert v1_orders.version_num == 1

    # Verify audit event recorded
    stmt_audit = select(AuditLog).where(
        AuditLog.action == "BULK_METADATA_INGESTION",
        AuditLog.entity_id == resp_1.schema_id,
    )
    audit_rec = (await test_db_session.execute(stmt_audit)).scalar_one_or_none()
    assert audit_rec is not None
    assert audit_rec.actor_username == user.username

    # Step 2: Human Curation
    # Curate approved_description on table and column
    orders_tbl.approved_description = "Curated customer orders business table"
    stmt_col = select(CatalogColumn).where(
        CatalogColumn.table_id == orders_tbl.id,
        CatalogColumn.name == "amount",
    )
    col_amount = (await test_db_session.execute(stmt_col)).scalar_one()
    col_amount.approved_description = "Curated amount: total transaction value"
    await test_db_session.flush()

    # Batch 2: Schema Evolution
    # - orders table:
    #   - source_description updated
    #   - column 'amount': data_type modified from INT to DECIMAL(12,2)
    #   - column 'currency': NEW column added
    #   - column 'notes': REMOVED from payload (should be soft-deleted as DELETED)
    # - customers table: REMOVED from payload (should be soft-deleted as DELETED)
    batch_2 = BulkIngestionRequest(
        connection_id=connection.id,
        schema_name=schema_name,
        tables=[
            IngestTablePayload(
                name="orders",
                table_type=TableType.TABLE,
                row_count_estimate=12000,
                size_bytes=1500000,
                source_description="Updated orders table comment from Postgres",
                owner="sales_team",
                columns=[
                    IngestColumnPayload(
                        name="order_id",
                        ordinal_position=1,
                        data_type="BIGINT",
                        raw_data_type="bigint",
                        is_nullable=False,
                        is_primary_key=True,
                        is_foreign_key=False,
                        source_description="Order ID surrogate key",
                        classification_level=ClassificationLevel.INTERNAL,
                    ),
                    IngestColumnPayload(
                        name="amount",
                        ordinal_position=2,
                        data_type="DECIMAL(12,2)",
                        raw_data_type="decimal(12,2)",
                        is_nullable=False,
                        is_primary_key=False,
                        is_foreign_key=False,
                        source_description="Updated amount comment from source DB",
                        classification_level=ClassificationLevel.INTERNAL,
                    ),
                    IngestColumnPayload(
                        name="currency",
                        ordinal_position=3,
                        data_type="VARCHAR(3)",
                        raw_data_type="varchar(3)",
                        is_nullable=False,
                        is_primary_key=False,
                        is_foreign_key=False,
                        source_description="ISO 4217 Currency Code",
                        classification_level=ClassificationLevel.INTERNAL,
                    ),
                ],
            )
        ],
    )

    resp_2 = await bulk_ingestion_service.ingest_bulk_metadata(
        session=test_db_session,
        payload=batch_2,
        actor_id=user.id,
        actor_username=user.username,
    )

    assert resp_2.success is True
    assert resp_2.tables_upserted == 1
    assert resp_2.tables_deleted == 1  # customers table was soft-deleted
    assert resp_2.columns_deleted == 1  # notes column was soft-deleted

    # 1. Assert approved_description was NOT overwritten or cleared
    await test_db_session.refresh(orders_tbl)
    assert orders_tbl.approved_description == "Curated customer orders business table"
    assert orders_tbl.source_description == "Updated orders table comment from Postgres"

    await test_db_session.refresh(col_amount)
    assert col_amount.approved_description == "Curated amount: total transaction value"
    assert col_amount.source_description == "Updated amount comment from source DB"
    assert col_amount.data_type == "DECIMAL(12,2)"

    # 2. Assert soft-deleted entities
    stmt_cust = select(CatalogTable).where(
        CatalogTable.schema_id == resp_1.schema_id,
        CatalogTable.name == "customers",
    )
    cust_tbl = (await test_db_session.execute(stmt_cust)).scalar_one()
    assert cust_tbl.status == LifecycleStatus.DELETED

    stmt_notes = select(CatalogColumn).where(
        CatalogColumn.table_id == orders_tbl.id,
        CatalogColumn.name == "notes",
    )
    col_notes = (await test_db_session.execute(stmt_notes)).scalar_one()
    assert col_notes.status == LifecycleStatus.DELETED

    # 3. Assert newly added column
    stmt_curr = select(CatalogColumn).where(
        CatalogColumn.table_id == orders_tbl.id,
        CatalogColumn.name == "currency",
    )
    col_curr = (await test_db_session.execute(stmt_curr)).scalar_one()
    assert col_curr.status == LifecycleStatus.ACTIVE

    # 4. Assert new TableVersion created
    stmt_v2 = (
        select(TableVersion)
        .where(TableVersion.table_id == orders_tbl.id)
        .order_by(TableVersion.version_num.desc())
    )
    versions = list((await test_db_session.execute(stmt_v2)).scalars().all())
    assert len(versions) == 2
    assert versions[0].version_num == 2
    v2_orders = versions[0]

    # 5. Assert SchemaChangeLog records created
    stmt_logs = select(SchemaChangeLog).where(
        SchemaChangeLog.version_id == v2_orders.id
    )
    logs = list((await test_db_session.execute(stmt_logs)).scalars().all())
    log_types = {log.change_type for log in logs}

    assert SchemaChangeType.COLUMN_ADDED in log_types
    assert SchemaChangeType.COLUMN_DROPPED in log_types
    assert SchemaChangeType.TYPE_CHANGED in log_types

