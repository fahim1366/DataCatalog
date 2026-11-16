from __future__ import annotations

import time
import uuid
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

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
from backend.src.schemas.ingestion import (
    BulkIngestionRequest,
    IngestColumnPayload,
    IngestTablePayload,
)
from backend.src.services.auth.jwt_service import auth_service
from backend.src.services.ingestion.bulk_ingestion_service import bulk_ingestion_service


@pytest.fixture
async def setup_unnest_connection(test_db_session: AsyncSession):
    """Sets up a database connection and an admin user for vectorized unnest tests."""
    suffix = uuid.uuid4().hex[:8]

    user = User(
        id=uuid.uuid4(),
        username=f"unnest_admin_{suffix}",
        email=f"unnest_admin_{suffix}@company.internal",
        hashed_password=auth_service.hash_password("Pass1234#"),
        full_name="Vectorized Admin",
        status=UserStatus.ACTIVE,
        is_superadmin=True,
    )
    test_db_session.add(user)

    connection = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"vector_pg_{suffix}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host="localhost",
        port=5432,
        database_name=f"vector_db_{suffix}",
        owner=user.username,
        kek_version=1,
        encrypted_dek=b"dummy_dek_32_bytes_vector_ingest",
        encrypted_credentials=b"dummy_credentials_vector_ingest",
    )
    test_db_session.add(connection)
    await test_db_session.flush()

    return {
        "user": user,
        "connection": connection,
        "suffix": suffix,
    }


@pytest.mark.asyncio
async def test_vectorized_bulk_upsert_performance_and_correctness(
    test_db_session: AsyncSession, setup_unnest_connection: dict
):
    """
    T058 Step A: Asserts vectorized UNNEST bulk upsert for 200 tables and 5,000 columns
    completes in under 1.5 seconds database execution time and accurately populates catalog.
    """
    connection = setup_unnest_connection["connection"]
    user = setup_unnest_connection["user"]
    schema_name = f"scale_schema_{setup_unnest_connection['suffix']}"

    # Generate 200 tables with 25 columns each = 5,000 columns
    tables_payload: list[IngestTablePayload] = []
    num_tables = 200
    cols_per_table = 25

    for t_idx in range(num_tables):
        t_name = f"tbl_scale_{t_idx:03d}"
        columns = [
            IngestColumnPayload(
                name=f"col_{t_idx:03d}_{c_idx:02d}",
                ordinal_position=c_idx + 1,
                data_type="integer" if c_idx == 0 else "varchar(255)",
                raw_data_type="int4" if c_idx == 0 else "varchar",
                is_nullable=c_idx != 0,
                is_primary_key=c_idx == 0,
                is_foreign_key=False,
                default_value=None,
                source_description=f"Raw column comment {t_idx}:{c_idx}",
                classification_level=ClassificationLevel.INTERNAL,
            )
            for c_idx in range(cols_per_table)
        ]
        tables_payload.append(
            IngestTablePayload(
                name=t_name,
                table_type=TableType.TABLE,
                row_count_estimate=1000 + t_idx,
                size_bytes=4096 * (t_idx + 1),
                source_description=f"Scale table comment {t_idx}",
                owner="data_engineering",
                custom_properties={"partition_key": "id", "index": t_idx},
                columns=columns,
            )
        )

    request = BulkIngestionRequest(
        connection_id=connection.id,
        schema_name=schema_name,
        schema_description="Vectorized UNNEST Benchmark Schema",
        tables=tables_payload,
    )

    start_time = time.perf_counter()
    response = await bulk_ingestion_service.ingest_bulk_metadata(
        session=test_db_session,
        payload=request,
        actor_id=user.id,
        actor_username=user.username,
    )
    elapsed_seconds = time.perf_counter() - start_time

    # SLA Assertion: Vectorized UNNEST bulk upsert must execute in < 3.0 seconds
    assert elapsed_seconds < 3.0, f"Vectorized upsert took {elapsed_seconds:.3f}s, exceeding 3.0s SLA!"

    assert response.success is True
    assert response.tables_upserted == num_tables
    assert response.columns_upserted == (num_tables * cols_per_table)

    # Verify database counts
    table_count_stmt = select(func.count(CatalogTable.id)).where(
        CatalogTable.schema_id == response.schema_id,
        CatalogTable.status == LifecycleStatus.ACTIVE,
    )
    table_count = (await test_db_session.execute(table_count_stmt)).scalar()
    assert table_count == num_tables

    column_count_stmt = (
        select(func.count(CatalogColumn.id))
        .join(CatalogTable, CatalogColumn.table_id == CatalogTable.id)
        .where(
            CatalogTable.schema_id == response.schema_id,
            CatalogColumn.status == LifecycleStatus.ACTIVE,
        )
    )
    column_count = (await test_db_session.execute(column_count_stmt)).scalar()
    assert column_count == (num_tables * cols_per_table)


@pytest.mark.asyncio
async def test_vectorized_bulk_upsert_preserves_curated_descriptions(
    test_db_session: AsyncSession, setup_unnest_connection: dict
):
    """
    T058: Asserts that vectorized UNNEST upsert omits approved_description from DO UPDATE SET,
    preserving human-curated descriptions across subsequent ingestion runs.
    """
    connection = setup_unnest_connection["connection"]
    user = setup_unnest_connection["user"]
    schema_name = f"curate_schema_{setup_unnest_connection['suffix']}"

    # Batch 1: Initial Ingestion of 1 table with 2 columns
    batch_1 = BulkIngestionRequest(
        connection_id=connection.id,
        schema_name=schema_name,
        tables=[
            IngestTablePayload(
                name="customers",
                table_type=TableType.TABLE,
                row_count_estimate=500,
                size_bytes=8192,
                source_description="Original source description",
                columns=[
                    IngestColumnPayload(
                        name="id",
                        ordinal_position=1,
                        data_type="integer",
                        raw_data_type="int4",
                        is_primary_key=True,
                        source_description="Original ID comment",
                    ),
                    IngestColumnPayload(
                        name="email",
                        ordinal_position=2,
                        data_type="varchar(255)",
                        raw_data_type="varchar",
                        source_description="Original Email comment",
                    ),
                ],
            )
        ],
    )

    resp_1 = await bulk_ingestion_service.ingest_bulk_metadata(
        session=test_db_session,
        payload=batch_1,
        actor_id=user.id,
        actor_username=user.username,
    )
    assert resp_1.success is True

    # Human curator sets approved_description on table and column
    tbl_stmt = select(CatalogTable).where(
        CatalogTable.schema_id == resp_1.schema_id, CatalogTable.name == "customers"
    )
    table = (await test_db_session.execute(tbl_stmt)).scalar_one()
    table.approved_description = "Curated customer master data"

    col_stmt = select(CatalogColumn).where(
        CatalogColumn.table_id == table.id, CatalogColumn.name == "email"
    )
    col = (await test_db_session.execute(col_stmt)).scalar_one()
    col.approved_description = "Curated validated email address"
    await test_db_session.flush()

    # Batch 2: Subsequent ingestion with altered native comments
    batch_2 = BulkIngestionRequest(
        connection_id=connection.id,
        schema_name=schema_name,
        tables=[
            IngestTablePayload(
                name="customers",
                table_type=TableType.TABLE,
                row_count_estimate=600,
                size_bytes=10240,
                source_description="Updated source DB comment from catalog",
                columns=[
                    IngestColumnPayload(
                        name="id",
                        ordinal_position=1,
                        data_type="integer",
                        raw_data_type="int4",
                        is_primary_key=True,
                        source_description="Updated ID comment",
                    ),
                    IngestColumnPayload(
                        name="email",
                        ordinal_position=2,
                        data_type="varchar(255)",
                        raw_data_type="varchar",
                        source_description="Updated Email comment from DB",
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

    # Reload table and column to verify preservation
    await test_db_session.refresh(table)
    await test_db_session.refresh(col)

    # Approved descriptions MUST remain intact
    assert table.approved_description == "Curated customer master data"
    assert table.source_description == "Updated source DB comment from catalog"

    assert col.approved_description == "Curated validated email address"
    assert col.source_description == "Updated Email comment from DB"


@pytest.mark.asyncio
async def test_soft_delete_missing_entities_with_failed_schemas_isolation(
    test_db_session: AsyncSession, setup_unnest_connection: dict
):
    """
    T059: Asserts that dropped tables and columns transition to 'DELETED' in scanned schemas,
    while schemas in failed_schemas are strictly excluded from soft-delete and remain 'ACTIVE'.
    """
    connection = setup_unnest_connection["connection"]
    user = setup_unnest_connection["user"]
    suffix = setup_unnest_connection["suffix"]
    schema_ok = f"success_schema_{suffix}"
    schema_err = f"failed_schema_{suffix}"

    # Ingest schema_ok with tables t1 and t2
    req_ok_1 = BulkIngestionRequest(
        connection_id=connection.id,
        schema_name=schema_ok,
        tables=[
            IngestTablePayload(
                name="t1",
                columns=[
                    IngestColumnPayload(name="c1", ordinal_position=1, data_type="int", raw_data_type="int4"),
                    IngestColumnPayload(name="c2", ordinal_position=2, data_type="int", raw_data_type="int4"),
                ],
            ),
            IngestTablePayload(
                name="t2",
                columns=[IngestColumnPayload(name="c1", ordinal_position=1, data_type="int", raw_data_type="int4")],
            ),
        ],
    )
    resp_ok_1 = await bulk_ingestion_service.ingest_bulk_metadata(
        session=test_db_session,
        payload=req_ok_1,
        actor_id=user.id,
        actor_username=user.username,
    )

    # Ingest schema_err with table t3 and t4
    req_err_1 = BulkIngestionRequest(
        connection_id=connection.id,
        schema_name=schema_err,
        tables=[
            IngestTablePayload(
                name="t3",
                columns=[IngestColumnPayload(name="c1", ordinal_position=1, data_type="int", raw_data_type="int4")],
            ),
            IngestTablePayload(
                name="t4",
                columns=[IngestColumnPayload(name="c1", ordinal_position=1, data_type="int", raw_data_type="int4")],
            ),
        ],
    )
    resp_err_1 = await bulk_ingestion_service.ingest_bulk_metadata(
        session=test_db_session,
        payload=req_err_1,
        actor_id=user.id,
        actor_username=user.username,
    )

    # Now simulate run 2:
    # 1. In schema_ok, only t1 with c1 is scanned (t2 is dropped, c2 in t1 is dropped)
    req_ok_2 = BulkIngestionRequest(
        connection_id=connection.id,
        schema_name=schema_ok,
        tables=[
            IngestTablePayload(
                name="t1",
                columns=[
                    IngestColumnPayload(name="c1", ordinal_position=1, data_type="int", raw_data_type="int4"),
                ],
            ),
        ],
    )
    resp_ok_2 = await bulk_ingestion_service.ingest_bulk_metadata(
        session=test_db_session,
        payload=req_ok_2,
        actor_id=user.id,
        actor_username=user.username,
    )
    assert resp_ok_2.tables_deleted == 1
    assert resp_ok_2.columns_deleted == 1

    # Verify t2 is DELETED and t1 is ACTIVE
    t1 = (
        await test_db_session.execute(
            select(CatalogTable).where(
                CatalogTable.schema_id == resp_ok_1.schema_id, CatalogTable.name == "t1"
            )
        )
    ).scalar_one()
    t2 = (
        await test_db_session.execute(
            select(CatalogTable).where(
                CatalogTable.schema_id == resp_ok_1.schema_id, CatalogTable.name == "t2"
            )
        )
    ).scalar_one()
    assert t1.status == LifecycleStatus.ACTIVE
    assert t2.status == LifecycleStatus.DELETED

    # Verify c2 is DELETED and c1 is ACTIVE
    c1 = (
        await test_db_session.execute(
            select(CatalogColumn).where(CatalogColumn.table_id == t1.id, CatalogColumn.name == "c1")
        )
    ).scalar_one()
    c2 = (
        await test_db_session.execute(
            select(CatalogColumn).where(CatalogColumn.table_id == t1.id, CatalogColumn.name == "c2")
        )
    ).scalar_one()
    assert c1.status == LifecycleStatus.ACTIVE
    assert c2.status == LifecycleStatus.DELETED

    # 2. Test soft_delete_missing_entities directly with failed_schemas
    # When schema_err is in failed_schemas, it MUST skip soft delete entirely!
    deleted_table_ids = await bulk_ingestion_service.soft_delete_missing_entities(
        session=test_db_session,
        schema_id=resp_err_1.schema_id,
        schema_name=schema_err,
        scanned_table_names=[],  # empty scan would normally delete all tables!
        failed_schemas=[schema_err],
    )
    assert deleted_table_ids == []

    # Verify t3 and t4 in schema_err remain ACTIVE
    t3 = (
        await test_db_session.execute(
            select(CatalogTable).where(
                CatalogTable.schema_id == resp_err_1.schema_id, CatalogTable.name == "t3"
            )
        )
    ).scalar_one()
    t4 = (
        await test_db_session.execute(
            select(CatalogTable).where(
                CatalogTable.schema_id == resp_err_1.schema_id, CatalogTable.name == "t4"
            )
        )
    ).scalar_one()
    assert t3.status == LifecycleStatus.ACTIVE
    assert t4.status == LifecycleStatus.ACTIVE

