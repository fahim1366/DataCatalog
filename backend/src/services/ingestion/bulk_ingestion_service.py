from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional, Set
import uuid

from sqlalchemy import func, select, text
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
    High-throughput batch metadata ingestion service utilizing PostgreSQL vectorized UNNEST array upserts
    to process catalog tables and columns at enterprise scale (< 1.5s for 200 tables / 5,000 columns).

    Guarantees:
    - Vectorized PostgreSQL UNNEST batch upsert for tables and columns in constant round-trips.
    - Never overwrites or clears human-curated approved_description values.
    - Soft-deletes missing tables and columns with schema-level partial failure safety guarantee (skipping failed_schemas).
    - In-place statistical metrics recording in table_statistics_history without bumping structural TableVersion.
    - Automatically triggers TableVersion snapshots and SchemaChangeLog entries exclusively on structural mutations.
    - Persists an immutable AuditLog record in the same atomic transaction.
    """

    async def record_table_statistics(
        self,
        session: AsyncSession,
        table_ids: List[uuid.UUID],
        row_counts: List[int],
        sizes: List[int],
        sync_job_id: Optional[uuid.UUID] = None,
    ) -> int:
        """
        Batch inserts extracted table statistics into table_statistics_history via UNNEST.
        Decoupled from structural TableVersion snapshotting.
        """
        if not table_ids:
            return 0

        stmt = text("""
            INSERT INTO table_statistics_history (
                id, table_id, recorded_at, row_count_estimate, size_bytes, sync_job_id
            )
            SELECT 
                gen_random_uuid(), v.table_id, clock_timestamp(), v.row_count, v.size, CAST(:sync_job_id AS uuid)
            FROM UNNEST(
                CAST(:table_ids AS uuid[]),
                CAST(:row_counts AS bigint[]),
                CAST(:sizes AS bigint[])
            ) AS v(table_id, row_count, size)
            RETURNING id;
        """)

        res = await session.execute(
            stmt,
            {
                "table_ids": table_ids,
                "row_counts": row_counts,
                "sizes": sizes,
                "sync_job_id": sync_job_id,
            },
        )
        return len(res.scalars().all())

    async def soft_delete_missing_entities(
        self,
        session: AsyncSession,
        schema_id: uuid.UUID,
        schema_name: str,
        scanned_table_names: List[str],
        failed_schemas: Optional[List[str]] = None,
    ) -> List[uuid.UUID]:
        """
        Set-based soft deletion with schema-level partial failure isolation guarantee.
        Precondition: skip soft-delete entirely for any schema present in failed_schemas.
        """
        if failed_schemas and schema_name in failed_schemas:
            logger.warning(
                f"Skipping soft deletion for schema '{schema_name}' as it is present in failed_schemas: {failed_schemas}"
            )
            return []

        if scanned_table_names:
            stmt = text("""
                UPDATE catalog_tables
                SET status = 'DELETED', updated_at = clock_timestamp()
                WHERE schema_id = :schema_id
                  AND name != ALL(CAST(:scanned_table_names AS varchar[]))
                  AND status != 'DELETED'
                RETURNING id;
            """)
            res = await session.execute(
                stmt,
                {"schema_id": schema_id, "scanned_table_names": scanned_table_names},
            )
        else:
            stmt = text("""
                UPDATE catalog_tables
                SET status = 'DELETED', updated_at = clock_timestamp()
                WHERE schema_id = :schema_id
                  AND status != 'DELETED'
                RETURNING id;
            """)
            res = await session.execute(stmt, {"schema_id": schema_id})

        return list(res.scalars().all())

    async def ingest_bulk_metadata(
        self,
        session: AsyncSession,
        payload: BulkIngestionRequest,
        actor_id: Optional[uuid.UUID] = None,
        actor_username: str = "system",
        sync_job_id: Optional[uuid.UUID] = None,
        failed_schemas: Optional[List[str]] = None,
    ) -> BulkIngestionResponse:
        """
        Executes vectorized bulk ingestion of tables and columns for a given connection and schema.
        Uses PostgreSQL UNNEST array upserts for constant round-trip execution.
        """
        effective_sync_job_id = sync_job_id or payload.sync_job_id
        effective_failed_schemas = failed_schemas or payload.failed_schemas or []

        # Schema-level partial failure check
        if payload.schema_name in effective_failed_schemas:
            logger.warning(
                f"Skipping ingestion for schema '{payload.schema_name}' as it is marked in failed_schemas."
            )
            return BulkIngestionResponse(
                success=False,
                connection_id=payload.connection_id,
                schema_id=uuid.uuid4(),
                schema_name=payload.schema_name,
                tables_upserted=0,
                tables_deleted=0,
                columns_upserted=0,
                columns_deleted=0,
                versions_created=0,
            )

        # 1. Verify connection existence
        conn_stmt = select(DatabaseConnection).where(
            DatabaseConnection.id == payload.connection_id
        )
        conn_res = await session.execute(conn_stmt)
        connection = conn_res.scalar_one_or_none()
        if not connection:
            raise ValueError(f"Database connection {payload.connection_id} not found.")

        if getattr(payload, "database_collation", None):
            connection.collation = payload.database_collation
            await session.flush()

        # 2. Get or create DatabaseSchema
        schema_stmt = select(DatabaseSchema).where(
            DatabaseSchema.connection_id == payload.connection_id,
            DatabaseSchema.name == payload.schema_name,
        )
        schema_res = await session.execute(schema_stmt)
        schema = schema_res.scalar_one_or_none()

        is_new_schema = False
        if not schema:
            is_new_schema = True
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

        # Check description drift on existing tables with approved_description
        if not is_new_schema:
            curated_tbl_stmt = select(CatalogTable).where(
                CatalogTable.schema_id == schema_id,
                CatalogTable.approved_description.isnot(None),
                CatalogTable.approved_description != "",
            )
            curated_tbl_res = await session.execute(curated_tbl_stmt)
            curated_tables = {t.name: t for t in curated_tbl_res.scalars().all()}

            if curated_tables and payload.tables:
                from backend.src.services.ingestion.drift_service import drift_service

                for t_payload in payload.tables:
                    if t_payload.name in curated_tables:
                        existing_t = curated_tables[t_payload.name]
                        await drift_service.detect_and_flag_drift(
                            session=session,
                            entity=existing_t,
                            extracted_comment=t_payload.source_description,
                            actor_id=actor_id,
                        )

        # 3. Vectorized Table Bulk Upsert via UNNEST (1 SQL Statement for all tables)
        if payload.tables:
            tbl_ids = [uuid.uuid4() for _ in payload.tables]
            schema_ids = [schema_id for _ in payload.tables]
            tbl_names = [t.name for t in payload.tables]
            tbl_types = [
                t.table_type.value if hasattr(t.table_type, "value") else str(t.table_type)
                for t in payload.tables
            ]
            row_counts = [t.row_count_estimate for t in payload.tables]
            sizes = [t.size_bytes for t in payload.tables]
            source_descriptions = [t.source_description for t in payload.tables]
            owners = [t.owner for t in payload.tables]
            custom_props = [json.dumps(t.custom_properties or {}) for t in payload.tables]

            table_upsert_stmt = text("""
                INSERT INTO catalog_tables (
                    id, schema_id, name, table_type, row_count_estimate, size_bytes,
                    source_description, owner, status, custom_properties, updated_at
                )
                SELECT 
                    v.id, v.schema_id, v.name, v.table_type::table_type_enum, v.row_count_estimate, v.size_bytes,
                    v.source_description, v.owner, 'ACTIVE'::lifecycle_status_enum, v.custom_properties::jsonb,
                    clock_timestamp()
                FROM UNNEST(
                    CAST(:ids AS uuid[]),
                    CAST(:schema_ids AS uuid[]),
                    CAST(:names AS varchar[]),
                    CAST(:table_types AS varchar[]),
                    CAST(:row_counts AS bigint[]),
                    CAST(:sizes AS bigint[]),
                    CAST(:source_descriptions AS text[]),
                    CAST(:owners AS varchar[]),
                    CAST(:custom_props AS text[])
                ) AS v(id, schema_id, name, table_type, row_count_estimate, size_bytes, source_description, owner, custom_properties)
                ON CONFLICT (schema_id, name) DO UPDATE SET
                    table_type = EXCLUDED.table_type,
                    row_count_estimate = EXCLUDED.row_count_estimate,
                    size_bytes = EXCLUDED.size_bytes,
                    source_description = EXCLUDED.source_description,
                    owner = COALESCE(EXCLUDED.owner, catalog_tables.owner),
                    status = 'ACTIVE',
                    custom_properties = EXCLUDED.custom_properties,
                    updated_at = clock_timestamp()
                RETURNING id, name;
            """)

            tbl_upsert_res = await session.execute(
                table_upsert_stmt,
                {
                    "ids": tbl_ids,
                    "schema_ids": schema_ids,
                    "names": tbl_names,
                    "table_types": tbl_types,
                    "row_counts": row_counts,
                    "sizes": sizes,
                    "source_descriptions": source_descriptions,
                    "owners": owners,
                    "custom_props": custom_props,
                },
            )

            for row in tbl_upsert_res.fetchall():
                t_id, t_name = row[0], row[1]
                table_id_map[t_name] = t_id
            tables_upserted_count = len(table_id_map)

        # Check description drift on existing columns with approved_description
        if not is_new_schema:
            curated_col_stmt = (
                select(CatalogColumn)
                .join(CatalogTable, CatalogColumn.table_id == CatalogTable.id)
                .where(
                    CatalogTable.schema_id == schema_id,
                    CatalogColumn.approved_description.isnot(None),
                    CatalogColumn.approved_description != "",
                )
            )
            curated_col_res = await session.execute(curated_col_stmt)
            curated_cols = {(c.table_id, c.name): c for c in curated_col_res.scalars().all()}

            if curated_cols and payload.tables:
                from backend.src.services.ingestion.drift_service import drift_service

                for t_payload in payload.tables:
                    cur_t_id = table_id_map.get(t_payload.name)
                    if not cur_t_id:
                        continue
                    for c_payload in t_payload.columns:
                        col_key = (cur_t_id, c_payload.name)
                        if col_key in curated_cols:
                            existing_c = curated_cols[col_key]
                            await drift_service.detect_and_flag_drift(
                                session=session,
                                entity=existing_c,
                                extracted_comment=c_payload.source_description,
                                actor_id=actor_id,
                            )

        # 4. Vectorized Column Bulk Upsert via UNNEST (1 SQL Statement for all columns across all tables)
        col_ids: List[uuid.UUID] = []
        col_table_ids: List[uuid.UUID] = []
        col_names: List[str] = []
        col_ordinal_positions: List[int] = []
        col_data_types: List[str] = []
        col_raw_data_types: List[str] = []
        col_collations: List[Optional[str]] = []
        col_is_nullables: List[bool] = []
        col_is_primary_keys: List[bool] = []
        col_is_foreign_keys: List[bool] = []
        col_default_values: List[Optional[str]] = []
        col_source_descriptions: List[Optional[str]] = []
        col_classification_levels: List[str] = []

        for table_payload in payload.tables:
            current_table_id = table_id_map.get(table_payload.name)
            if not current_table_id:
                continue

            for col_payload in table_payload.columns:
                cls_level_str = (
                    col_payload.classification_level.value
                    if hasattr(col_payload.classification_level, "value")
                    else str(col_payload.classification_level)
                )
                col_ids.append(uuid.uuid4())
                col_table_ids.append(current_table_id)
                col_names.append(col_payload.name)
                col_ordinal_positions.append(col_payload.ordinal_position)
                col_data_types.append(col_payload.data_type)
                col_raw_data_types.append(col_payload.raw_data_type)
                col_collations.append(col_payload.collation)
                col_is_nullables.append(col_payload.is_nullable)
                col_is_primary_keys.append(col_payload.is_primary_key)
                col_is_foreign_keys.append(col_payload.is_foreign_key)
                col_default_values.append(col_payload.default_value)
                col_source_descriptions.append(col_payload.source_description)
                col_classification_levels.append(cls_level_str)

        if col_ids:
            col_upsert_stmt = text("""
                INSERT INTO catalog_columns (
                    id, table_id, name, ordinal_position, data_type, raw_data_type,
                    "collation", is_nullable, is_primary_key, is_foreign_key, default_value,
                    source_description, classification_level, status, updated_at
                )
                SELECT 
                    v.id, v.table_id, v.name, v.ordinal_position, v.data_type, v.raw_data_type,
                    v."collation", v.is_nullable, v.is_primary_key, v.is_foreign_key, v.default_value,
                    v.source_description, v.classification_level::classification_level_enum, 'ACTIVE'::lifecycle_status_enum,
                    clock_timestamp()
                FROM UNNEST(
                    CAST(:ids AS uuid[]),
                    CAST(:table_ids AS uuid[]),
                    CAST(:names AS varchar[]),
                    CAST(:ordinal_positions AS int[]),
                    CAST(:data_types AS varchar[]),
                    CAST(:raw_data_types AS varchar[]),
                    CAST(:collations AS varchar[]),
                    CAST(:is_nullables AS boolean[]),
                    CAST(:is_primary_keys AS boolean[]),
                    CAST(:is_foreign_keys AS boolean[]),
                    CAST(:default_values AS text[]),
                    CAST(:source_descriptions AS text[]),
                    CAST(:classification_levels AS varchar[])
                ) AS v(id, table_id, name, ordinal_position, data_type, raw_data_type, "collation", is_nullable, is_primary_key, is_foreign_key, default_value, source_description, classification_level)
                ON CONFLICT (table_id, name) DO UPDATE SET
                    ordinal_position = EXCLUDED.ordinal_position,
                    data_type = EXCLUDED.data_type,
                    raw_data_type = EXCLUDED.raw_data_type,
                    "collation" = EXCLUDED."collation",
                    is_nullable = EXCLUDED.is_nullable,
                    is_primary_key = EXCLUDED.is_primary_key,
                    is_foreign_key = EXCLUDED.is_foreign_key,
                    default_value = EXCLUDED.default_value,
                    source_description = EXCLUDED.source_description,
                    classification_level = EXCLUDED.classification_level,
                    status = 'ACTIVE',
                    updated_at = clock_timestamp()
                RETURNING id;
            """)

            col_upsert_res = await session.execute(
                col_upsert_stmt,
                {
                    "ids": col_ids,
                    "table_ids": col_table_ids,
                    "names": col_names,
                    "ordinal_positions": col_ordinal_positions,
                    "data_types": col_data_types,
                    "raw_data_types": col_raw_data_types,
                    "collations": col_collations,
                    "is_nullables": col_is_nullables,
                    "is_primary_keys": col_is_primary_keys,
                    "is_foreign_keys": col_is_foreign_keys,
                    "default_values": col_default_values,
                    "source_descriptions": col_source_descriptions,
                    "classification_levels": col_classification_levels,
                },
            )
            columns_upserted_count = len(col_upsert_res.scalars().all())

        # 4b. Upsert Indexes
        if payload.indexes:
            idx_table_ids: List[uuid.UUID] = []
            idx_names: List[str] = []
            idx_uniques: List[bool] = []
            idx_primaries: List[bool] = []
            idx_types: List[str] = []
            idx_columns_json: List[str] = []
            idx_definitions: List[Optional[str]] = []

            for idx_p in payload.indexes:
                target_tid = table_id_map.get(idx_p.table_name)
                if not target_tid:
                    continue
                idx_table_ids.append(target_tid)
                idx_names.append(idx_p.name)
                idx_uniques.append(idx_p.is_unique)
                idx_primaries.append(idx_p.is_primary)
                idx_types.append(idx_p.index_type or "BTREE")
                idx_columns_json.append(json.dumps(idx_p.columns_list or []))
                idx_definitions.append(idx_p.definition)

            if idx_table_ids:
                idx_upsert_stmt = text("""
                    INSERT INTO catalog_indexes (
                        id, table_id, schema_id, name, is_unique, is_primary, index_type,
                        columns_list, definition, updated_at
                    )
                    SELECT
                        gen_random_uuid(), v.table_id, :schema_id, v.name, v.is_unique, v.is_primary,
                        v.index_type, v.columns_list::jsonb, v.definition, clock_timestamp()
                    FROM UNNEST(
                        CAST(:table_ids AS uuid[]),
                        CAST(:names AS varchar[]),
                        CAST(:is_uniques AS boolean[]),
                        CAST(:is_primaries AS boolean[]),
                        CAST(:index_types AS varchar[]),
                        CAST(:columns_lists AS text[]),
                        CAST(:definitions AS text[])
                    ) AS v(table_id, name, is_unique, is_primary, index_type, columns_list, definition)
                    ON CONFLICT (table_id, name) DO UPDATE SET
                        is_unique = EXCLUDED.is_unique,
                        is_primary = EXCLUDED.is_primary,
                        index_type = EXCLUDED.index_type,
                        columns_list = EXCLUDED.columns_list,
                        definition = EXCLUDED.definition,
                        updated_at = clock_timestamp();
                """)
                await session.execute(
                    idx_upsert_stmt,
                    {
                        "schema_id": schema_id,
                        "table_ids": idx_table_ids,
                        "names": idx_names,
                        "is_uniques": idx_uniques,
                        "is_primaries": idx_primaries,
                        "index_types": idx_types,
                        "columns_lists": idx_columns_json,
                        "definitions": idx_definitions,
                    },
                )

        # 4c. Upsert Routines
        if payload.routines:
            rt_names: List[str] = []
            rt_types: List[str] = []
            rt_languages: List[str] = []
            rt_signatures: List[Optional[str]] = []
            rt_returns: List[Optional[str]] = []
            rt_descriptions: List[Optional[str]] = []

            for r_p in payload.routines:
                rt_names.append(r_p.name)
                rt_types.append(r_p.routine_type or "FUNCTION")
                rt_languages.append(r_p.language or "sql")
                rt_signatures.append(r_p.arguments_signature)
                rt_returns.append(r_p.return_type)
                rt_descriptions.append(r_p.source_description)

            if rt_names:
                rt_upsert_stmt = text("""
                    INSERT INTO catalog_routines (
                        id, schema_id, name, routine_type, language, arguments_signature,
                        return_type, source_description, updated_at
                    )
                    SELECT
                        gen_random_uuid(), :schema_id, v.name, v.routine_type, v.language,
                        v.arguments_signature, v.return_type, v.source_description, clock_timestamp()
                    FROM UNNEST(
                        CAST(:names AS varchar[]),
                        CAST(:routine_types AS varchar[]),
                        CAST(:languages AS varchar[]),
                        CAST(:arguments_signatures AS text[]),
                        CAST(:return_types AS varchar[]),
                        CAST(:source_descriptions AS text[])
                    ) AS v(name, routine_type, language, arguments_signature, return_type, source_description)
                    ON CONFLICT (schema_id, name, routine_type) DO UPDATE SET
                        language = EXCLUDED.language,
                        arguments_signature = EXCLUDED.arguments_signature,
                        return_type = EXCLUDED.return_type,
                        source_description = EXCLUDED.source_description,
                        updated_at = clock_timestamp();
                """)
                await session.execute(
                    rt_upsert_stmt,
                    {
                        "schema_id": schema_id,
                        "names": rt_names,
                        "routine_types": rt_types,
                        "languages": rt_languages,
                        "arguments_signatures": rt_signatures,
                        "return_types": rt_returns,
                        "source_descriptions": rt_descriptions,
                    },
                )

        # 5. Soft-delete missing columns for scanned tables (Set-based vectorized sweep)
        scanned_table_ids = list(table_id_map.values())
        if scanned_table_ids:
            if col_ids:
                soft_del_col_stmt = text("""
                    WITH scanned AS (
                        SELECT UNNEST(CAST(:tbl_ids AS uuid[])) AS table_id,
                               UNNEST(CAST(:c_names AS varchar[])) AS col_name
                    )
                    UPDATE catalog_columns c
                    SET status = 'DELETED', updated_at = clock_timestamp()
                    WHERE c.table_id = ANY(CAST(:distinct_tbl_ids AS uuid[]))
                      AND c.status != 'DELETED'
                      AND NOT EXISTS (
                          SELECT 1 FROM scanned s WHERE s.table_id = c.table_id AND s.col_name = c.name
                      )
                    RETURNING c.id;
                """)
                soft_del_col_res = await session.execute(
                    soft_del_col_stmt,
                    {
                        "tbl_ids": col_table_ids,
                        "c_names": col_names,
                        "distinct_tbl_ids": scanned_table_ids,
                    },
                )
            else:
                soft_del_col_stmt = text("""
                    UPDATE catalog_columns
                    SET status = 'DELETED', updated_at = clock_timestamp()
                    WHERE table_id = ANY(CAST(:distinct_tbl_ids AS uuid[]))
                      AND status != 'DELETED'
                    RETURNING id;
                """)
                soft_del_col_res = await session.execute(
                    soft_del_col_stmt,
                    {"distinct_tbl_ids": scanned_table_ids},
                )
            columns_deleted_count = len(soft_del_col_res.scalars().all())

        # 6. Soft-delete missing tables for this schema with failed_schemas guard
        deleted_tbl_rows = await self.soft_delete_missing_entities(
            session=session,
            schema_id=schema_id,
            schema_name=schema_name,
            scanned_table_names=ingested_table_names,
            failed_schemas=effective_failed_schemas,
        )
        tables_deleted_count = len(deleted_tbl_rows)

        # 7. In-place stats recording into table_statistics_history (T060)
        if payload.tables:
            stat_table_ids = [table_id_map[t.name] for t in payload.tables if t.name in table_id_map]
            stat_row_counts = [t.row_count_estimate for t in payload.tables if t.name in table_id_map]
            stat_sizes = [t.size_bytes for t in payload.tables if t.name in table_id_map]
            await self.record_table_statistics(
                session=session,
                table_ids=stat_table_ids,
                row_counts=stat_row_counts,
                sizes=stat_sizes,
                sync_job_id=effective_sync_job_id,
            )

        await session.flush()

        # 8. Automatically trigger TableVersion snapshots and SchemaChangeLog entries
        # Only captures versions when structural schema changes occurred (T060 & T063)
        all_affected_table_ids = list(set(table_id_map.values()) | set(deleted_tbl_rows))
        created_versions = await version_service.capture_versions_batch(
            session=session,
            table_ids=all_affected_table_ids,
            actor_id=actor_id,
            actor_username=actor_username,
        )
        versions_created_count = len(created_versions)

        # 9. Record immutable audit log entry in the same transaction
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
