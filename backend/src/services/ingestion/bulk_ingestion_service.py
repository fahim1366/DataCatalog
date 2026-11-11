from __future__ import annotations

import logging
from typing import Dict, List, Optional
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.models.catalog import (
    CatalogColumn,
    CatalogTable,
    DatabaseSchema,
    LifecycleStatus,
)
from backend.src.models.connections import DatabaseConnection
from backend.src.models.rbac import EntityType
from backend.src.schemas.ingestion import (
    BulkIngestionRequest,
    BulkIngestionResponse,
)
from backend.src.services.audit.audit_service import record_audit_event
from backend.src.services.catalog.version_service import version_service

logger = logging.getLogger(__name__)


class BulkIngestionService:
    """
    High-throughput batch metadata ingestion service utilizing PostgreSQL batch upsert
    (INSERT ... ON CONFLICT DO UPDATE) to process catalog tables and columns at enterprise scale.

    Guarantees:
    - Never overwrites or clears human-curated approved_description values.
    - Soft-deletes missing tables and columns by transitioning status to DELETED.
    - Automatically triggers TableVersion snapshots and SchemaChangeLog entries on structural mutations.
    - Persists an immutable AuditLog record in the same atomic transaction.
    """

    async def ingest_bulk_metadata(
        self,
        session: AsyncSession,
        payload: BulkIngestionRequest,
        actor_id: Optional[uuid.UUID] = None,
        actor_username: str = "system",
    ) -> BulkIngestionResponse:
        """
        Executes bulk ingestion of tables and columns for a given connection and schema.
        """
        # 1. Verify connection existence
        conn_stmt = select(DatabaseConnection).where(
            DatabaseConnection.id == payload.connection_id
        )
        conn_res = await session.execute(conn_stmt)
        connection = conn_res.scalar_one_or_none()
        if not connection:
            raise ValueError(f"Database connection {payload.connection_id} not found.")

        # 2. Get or create DatabaseSchema
        schema_stmt = select(DatabaseSchema).where(
            DatabaseSchema.connection_id == payload.connection_id,
            DatabaseSchema.name == payload.schema_name,
        )
        schema_res = await session.execute(schema_stmt)
        schema = schema_res.scalar_one_or_none()

        if not schema:
            schema = DatabaseSchema(
                id=uuid.uuid4(),
                connection_id=payload.connection_id,
                name=payload.schema_name,
                description=payload.schema_description,
                status=LifecycleStatus.ACTIVE,
            )
            session.add(schema)
            await session.flush()
        else:
            if schema.status == LifecycleStatus.DELETED:
                schema.status = LifecycleStatus.ACTIVE
                schema.updated_at = func.clock_timestamp()
                await session.flush()

        schema_id = schema.id
        schema_name = schema.name

        tables_upserted_count = 0
        tables_deleted_count = 0
        columns_upserted_count = 0
        columns_deleted_count = 0
        versions_created_count = 0

        ingested_table_names = [t.name for t in payload.tables]
        table_id_map: Dict[str, uuid.UUID] = {}

        # 3. Batch Upsert Tables
        for table_payload in payload.tables:
            table_type_str = (
                table_payload.table_type.value
                if hasattr(table_payload.table_type, "value")
                else str(table_payload.table_type)
            )

            table_insert_stmt = pg_insert(CatalogTable).values(
                id=uuid.uuid4(),
                schema_id=schema.id,
                name=table_payload.name,
                table_type=table_type_str,
                row_count_estimate=table_payload.row_count_estimate,
                size_bytes=table_payload.size_bytes,
                source_description=table_payload.source_description,
                owner=table_payload.owner,
                status=LifecycleStatus.ACTIVE,
                custom_properties=table_payload.custom_properties,
            )

            # ON CONFLICT DO UPDATE:
            # Notice: approved_description is intentionally OMITTED to preserve human curation.
            table_upsert_stmt = table_insert_stmt.on_conflict_do_update(
                constraint="uq_schema_table",
                set_={
                    "table_type": table_insert_stmt.excluded.table_type,
                    "row_count_estimate": table_insert_stmt.excluded.row_count_estimate,
                    "size_bytes": table_insert_stmt.excluded.size_bytes,
                    "source_description": table_insert_stmt.excluded.source_description,
                    "owner": func.coalesce(
                        table_insert_stmt.excluded.owner, CatalogTable.owner
                    ),
                    "custom_properties": table_insert_stmt.excluded.custom_properties,
                    "status": LifecycleStatus.ACTIVE,
                    "updated_at": func.clock_timestamp(),
                },
            ).returning(CatalogTable.id, CatalogTable.name)

            table_upsert_res = await session.execute(table_upsert_stmt)
            row = table_upsert_res.first()
            if row:
                tbl_id, tbl_name = row
                table_id_map[tbl_name] = tbl_id
                tables_upserted_count += 1

            # 4. Batch Upsert Columns for this table
            current_table_id = table_id_map[table_payload.name]
            ingested_column_names = [c.name for c in table_payload.columns]

            for col_payload in table_payload.columns:
                cls_level_str = (
                    col_payload.classification_level.value
                    if hasattr(col_payload.classification_level, "value")
                    else str(col_payload.classification_level)
                )

                col_insert_stmt = pg_insert(CatalogColumn).values(
                    id=uuid.uuid4(),
                    table_id=current_table_id,
                    name=col_payload.name,
                    ordinal_position=col_payload.ordinal_position,
                    data_type=col_payload.data_type,
                    raw_data_type=col_payload.raw_data_type,
                    is_nullable=col_payload.is_nullable,
                    is_primary_key=col_payload.is_primary_key,
                    is_foreign_key=col_payload.is_foreign_key,
                    default_value=col_payload.default_value,
                    source_description=col_payload.source_description,
                    classification_level=cls_level_str,
                    status=LifecycleStatus.ACTIVE,
                )

                # ON CONFLICT DO UPDATE:
                # approved_description is preserved
                col_upsert_stmt = col_insert_stmt.on_conflict_do_update(
                    constraint="uq_table_column",
                    set_={
                        "ordinal_position": col_insert_stmt.excluded.ordinal_position,
                        "data_type": col_insert_stmt.excluded.data_type,
                        "raw_data_type": col_insert_stmt.excluded.raw_data_type,
                        "is_nullable": col_insert_stmt.excluded.is_nullable,
                        "is_primary_key": col_insert_stmt.excluded.is_primary_key,
                        "is_foreign_key": col_insert_stmt.excluded.is_foreign_key,
                        "default_value": col_insert_stmt.excluded.default_value,
                        "source_description": col_insert_stmt.excluded.source_description,
                        "classification_level": col_insert_stmt.excluded.classification_level,
                        "status": LifecycleStatus.ACTIVE,
                        "updated_at": func.clock_timestamp(),
                    },
                ).returning(CatalogColumn.id)

                col_upsert_res = await session.execute(col_upsert_stmt)
                if col_upsert_res.first():
                    columns_upserted_count += 1

            # 5. Soft-delete missing columns for this table
            if ingested_column_names:
                soft_del_col_stmt = (
                    update(CatalogColumn)
                    .where(
                        CatalogColumn.table_id == current_table_id,
                        CatalogColumn.name.not_in(ingested_column_names),
                        CatalogColumn.status != LifecycleStatus.DELETED,
                    )
                    .values(
                        status=LifecycleStatus.DELETED,
                        updated_at=func.clock_timestamp(),
                    )
                    .returning(CatalogColumn.id)
                )
            else:
                soft_del_col_stmt = (
                    update(CatalogColumn)
                    .where(
                        CatalogColumn.table_id == current_table_id,
                        CatalogColumn.status != LifecycleStatus.DELETED,
                    )
                    .values(
                        status=LifecycleStatus.DELETED,
                        updated_at=func.clock_timestamp(),
                    )
                    .returning(CatalogColumn.id)
                )
            soft_del_col_res = await session.execute(soft_del_col_stmt)
            deleted_col_rows = soft_del_col_res.scalars().all()
            columns_deleted_count += len(deleted_col_rows)

        # 6. Soft-delete missing tables for this schema
        if ingested_table_names:
            soft_del_tbl_stmt = (
                update(CatalogTable)
                .where(
                    CatalogTable.schema_id == schema.id,
                    CatalogTable.name.not_in(ingested_table_names),
                    CatalogTable.status != LifecycleStatus.DELETED,
                )
                .values(
                    status=LifecycleStatus.DELETED,
                    updated_at=func.clock_timestamp(),
                )
                .returning(CatalogTable.id)
            )
        else:
            soft_del_tbl_stmt = (
                update(CatalogTable)
                .where(
                    CatalogTable.schema_id == schema.id,
                    CatalogTable.status != LifecycleStatus.DELETED,
                )
                .values(
                    status=LifecycleStatus.DELETED,
                    updated_at=func.clock_timestamp(),
                )
                .returning(CatalogTable.id)
            )
        soft_del_tbl_res = await session.execute(soft_del_tbl_stmt)
        deleted_tbl_rows = list(soft_del_tbl_res.scalars().all())
        tables_deleted_count += len(deleted_tbl_rows)

        await session.flush()

        # 7. Automatically trigger TableVersion snapshots and SchemaChangeLog entries
        all_affected_table_ids = set(table_id_map.values()) | set(deleted_tbl_rows)
        for tbl_id in all_affected_table_ids:
            version_entry = await version_service.capture_version_if_changed(
                session=session,
                table_id=tbl_id,
                actor_id=actor_id,
                actor_username=actor_username,
            )
            if version_entry:
                versions_created_count += 1

        # 8. Record immutable audit log entry in the same transaction
        await record_audit_event(
            session=session,
            actor_username=actor_username,
            action="BULK_METADATA_INGESTION",
            entity_type=EntityType.SCHEMA,
            entity_id=schema_id,
            actor_id=actor_id,
            diff_jsonb={
                "connection_id": str(payload.connection_id),
                "schema_name": payload.schema_name,
                "tables_upserted": tables_upserted_count,
                "tables_deleted": tables_deleted_count,
                "columns_upserted": columns_upserted_count,
                "columns_deleted": columns_deleted_count,
                "versions_created": versions_created_count,
            },
            flush=True,
        )

        return BulkIngestionResponse(
            success=True,
            connection_id=payload.connection_id,
            schema_id=schema_id,
            schema_name=schema_name,
            tables_upserted=tables_upserted_count,
            tables_deleted=tables_deleted_count,
            columns_upserted=columns_upserted_count,
            columns_deleted=columns_deleted_count,
            versions_created=versions_created_count,
        )


bulk_ingestion_service = BulkIngestionService()
