from __future__ import annotations

import asyncio
import concurrent.futures
from datetime import datetime, timezone
import logging
import time
from typing import Any, Dict, List, Optional, Union
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.celery_app import (
    QUEUE_BULK,
    QUEUE_FAST,
    QUEUE_PRIORITY,
    celery_app,
)
from backend.src.core.database import get_session_context
from backend.src.models.catalog import TableType
from backend.src.models.connections import DatabaseConnection
from backend.src.models.sync_jobs import SyncJob, SyncJobStatus
from backend.src.schemas.ingestion import (
    BulkIngestionRequest,
    IngestColumnPayload,
    IngestIndexPayload,
    IngestRoutinePayload,
    IngestTablePayload,
)
from backend.src.services.connectors import default_connector_registry
from backend.src.services.crypto.envelope import EnvelopeEncryptionService
from backend.src.services.ingestion.bulk_ingestion_service import bulk_ingestion_service
from backend.src.services.workers.circuit_breaker import circuit_breaker_manager

logger = logging.getLogger(__name__)


def _run_coroutine(coro):
    """Safely executes an async coroutine from synchronous Celery worker processes."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(lambda: asyncio.run(coro)).result()
    else:
        return asyncio.run(coro)


async def test_connection_sync(
    connection_id: Union[uuid.UUID, str],
    session: Optional[AsyncSession] = None,
) -> Dict[str, Any]:
    """
    Executes an in-memory credential unwrapping and connector diagnostic test.
    Zero plaintext secrets written to disk, logs, or message payloads.
    """
    conn_uuid = uuid.UUID(str(connection_id)) if isinstance(connection_id, str) else connection_id

    async def _test(sess: AsyncSession) -> Dict[str, Any]:
        stmt = select(DatabaseConnection).where(DatabaseConnection.id == conn_uuid)
        res = await sess.execute(stmt)
        conn = res.scalar_one_or_none()
        if not conn:
            return {
                "success": False,
                "latency_ms": 0.0,
                "dialect": "UNKNOWN",
                "server_version": None,
                "message": f"DatabaseConnection {conn_uuid} not found.",
                "diagnostic_details": {"error": "NOT_FOUND"},
                "tested_at": datetime.now(timezone.utc).isoformat(),
            }

        crypto = EnvelopeEncryptionService()
        creds = crypto.decrypt_credentials(conn.encrypted_dek, conn.encrypted_credentials)

        config: Dict[str, Any] = {
            "host": conn.host,
            "port": conn.port,
            "database_name": conn.database_name,
            "custom_params": conn.custom_params or {},
            "username": creds.get("username", "postgres"),
            "password": creds.get("password", ""),
        }
        del creds  # Ephemeral memory zeroing

        try:
            connector = default_connector_registry.create_connector(conn.engine_type, config)
            async with connector:
                test_res = await connector.test_connection()

            return {
                "success": test_res.success,
                "latency_ms": test_res.latency_ms,
                "dialect": test_res.dialect,
                "server_version": test_res.server_version,
                "message": test_res.message,
                "diagnostic_details": test_res.diagnostic_details,
                "tested_at": test_res.tested_at.isoformat(),
            }
        except Exception as exc:
            logger.warning("Connection test failed for connection %s: %s", conn_uuid, exc)
            return {
                "success": False,
                "latency_ms": 0.0,
                "dialect": conn.engine_type.value if hasattr(conn.engine_type, "value") else str(conn.engine_type),
                "server_version": None,
                "message": f"Connection test failed: {str(exc)}",
                "diagnostic_details": {"error": str(exc)},
                "tested_at": datetime.now(timezone.utc).isoformat(),
            }

    if session is not None:
        return await _test(session)
    else:
        async with get_session_context() as sess:
            return await _test(sess)


async def execute_metadata_sync(
    connection_id: Union[uuid.UUID, str],
    job_id: Union[uuid.UUID, str],
    session: Optional[AsyncSession] = None,
) -> Dict[str, Any]:
    """
    Core metadata extraction and bulk ingestion execution orchestrator.

    Lifecycle:
    1. Status transitions: QUEUED -> RUNNING.
    2. Fetches encrypted credentials from catalog DB and unwraps in-memory.
    3. Connects to target DB via NullPool connector and extracts schema metadata.
    4. Applies vectorized UNNEST upserts via BulkIngestionService.
    5. Updates circuit breaker state (records success or consecutive failures).
    6. Commits final SyncJob metrics and sets status to SUCCESS / PARTIAL_FAILURE / FAILED.
    """
    conn_uuid = uuid.UUID(str(connection_id)) if isinstance(connection_id, str) else connection_id
    job_uuid = uuid.UUID(str(job_id)) if isinstance(job_id, str) else job_id

    async def _execute(sess: AsyncSession) -> Dict[str, Any]:
        stmt_job = select(SyncJob).where(SyncJob.id == job_uuid)
        res_job = await sess.execute(stmt_job)
        job = res_job.scalar_one_or_none()

        if not job:
            return {"success": False, "error": f"SyncJob {job_uuid} not found."}

        # Transition to RUNNING
        job.status = SyncJobStatus.RUNNING.value
        job.started_at = datetime.now(timezone.utc)
        await sess.commit()
        await sess.refresh(job)

        stmt_conn = select(DatabaseConnection).where(DatabaseConnection.id == conn_uuid)
        res_conn = await sess.execute(stmt_conn)
        conn = res_conn.scalar_one_or_none()

        if not conn:
            now = datetime.now(timezone.utc)
            job.status = SyncJobStatus.FAILED.value
            job.completed_at = now
            job.duration_ms = int((now - job.started_at).total_seconds() * 1000)
            job.error_message = f"DatabaseConnection {conn_uuid} not found."
            await sess.commit()
            return {"success": False, "error": job.error_message, "job_id": str(job.id)}

        # 1. Ephemeral credential decryption
        crypto = EnvelopeEncryptionService()
        try:
            creds = crypto.decrypt_credentials(conn.encrypted_dek, conn.encrypted_credentials)
        except Exception as exc:
            now = datetime.now(timezone.utc)
            job.status = SyncJobStatus.FAILED.value
            job.completed_at = now
            job.duration_ms = int((now - job.started_at).total_seconds() * 1000)
            job.error_message = f"Credential decryption failed: {str(exc)}"
            job.error_trace_jsonb = {"crypto_error": str(exc)}
            await sess.commit()
            await circuit_breaker_manager.record_job_outcome(
                sess, conn_uuid, success=False, error_message=job.error_message
            )
            return {
                "success": False,
                "status": job.status,
                "error": job.error_message,
                "job_id": str(job.id),
            }

        config: Dict[str, Any] = {
            "host": conn.host,
            "port": conn.port,
            "database_name": conn.database_name,
            "custom_params": conn.custom_params or {},
            "username": creds.get("username", "postgres"),
            "password": creds.get("password", ""),
        }
        del creds  # Ephemeral memory zeroing

        # 2. Connector Introspection
        extractions = []
        try:
            connector = default_connector_registry.create_connector(conn.engine_type, config)
            async with connector:
                extractions = await connector.extract_all_schemas()
        except Exception as exc:
            now = datetime.now(timezone.utc)
            job.status = SyncJobStatus.FAILED.value
            job.completed_at = now
            job.duration_ms = int((now - job.started_at).total_seconds() * 1000)
            job.error_message = f"Extraction failed: {str(exc)}"
            job.error_trace_jsonb = {"connector_error": str(exc)}
            await sess.commit()
            await circuit_breaker_manager.record_job_outcome(
                sess, conn_uuid, success=False, error_message=job.error_message
            )
            return {
                "success": False,
                "status": job.status,
                "error": job.error_message,
                "job_id": str(job.id),
            }

        # 3. Bulk Ingestion
        failed_schemas: List[str] = []
        schema_errors: Dict[str, Any] = {}
        for ext in extractions:
            if not ext.success:
                failed_schemas.append(ext.schema.name)
                schema_errors[ext.schema.name] = {
                    "error_message": ext.error_message,
                    "error_code": ext.error_code,
                }

        total_tables_scanned = 0
        total_columns_scanned = 0
        total_tables_upserted = 0
        total_tables_deleted = 0
        total_columns_upserted = 0
        total_columns_deleted = 0
        total_versions_created = 0

        for ext in extractions:
            if not ext.success:
                continue

            total_tables_scanned += len(ext.tables)
            total_columns_scanned += len(ext.columns)

            table_payloads: List[IngestTablePayload] = []
            for t in ext.tables:
                cols = [
                    IngestColumnPayload(
                        name=c.name,
                        ordinal_position=c.ordinal_position,
                        data_type=c.data_type,
                        raw_data_type=c.raw_data_type,
                        is_nullable=c.is_nullable,
                        is_primary_key=c.is_primary_key,
                        is_foreign_key=c.is_foreign_key,
                        default_value=c.default_value,
                        source_description=c.source_description,
                    )
                    for c in ext.columns
                    if c.table_name == t.name
                ]

                table_type_val = TableType.TABLE
                try:
                    table_type_val = TableType(t.table_type)
                except (ValueError, KeyError):
                    pass

                table_payloads.append(
                    IngestTablePayload(
                        name=t.name,
                        table_type=table_type_val,
                        row_count_estimate=t.row_count_estimate,
                        size_bytes=t.size_bytes,
                        source_description=t.source_description,
                        owner=t.owner,
                        custom_properties=t.custom_properties,
                        columns=cols,
                    )
                )

            idx_payloads = [
                IngestIndexPayload(
                    table_name=idx.table_name,
                    name=idx.name,
                    is_unique=idx.is_unique,
                    is_primary=idx.is_primary,
                    index_type=idx.index_type,
                    columns_list=idx.columns_list,
                    definition=idx.definition,
                )
                for idx in ext.indexes
            ]

            routine_payloads = [
                IngestRoutinePayload(
                    name=rt.name,
                    routine_type=rt.routine_type,
                    language=rt.language,
                    arguments_signature=rt.arguments_signature,
                    return_type=rt.return_type,
                    source_description=rt.source_description,
                )
                for rt in ext.routines
            ]

            req = BulkIngestionRequest(
                connection_id=conn.id,
                schema_name=ext.schema.name,
                schema_description=ext.schema.description,
                tables=table_payloads,
                indexes=idx_payloads,
                routines=routine_payloads,
                sync_job_id=job.id,
                failed_schemas=failed_schemas,
            )

            ingest_res = await bulk_ingestion_service.ingest_bulk_metadata(
                session=sess,
                payload=req,
                sync_job_id=job.id,
                failed_schemas=failed_schemas,
            )

            total_tables_upserted += ingest_res.tables_upserted
            total_tables_deleted += ingest_res.tables_deleted
            total_columns_upserted += ingest_res.columns_upserted
            total_columns_deleted += ingest_res.columns_deleted
            total_versions_created += ingest_res.versions_created

        # 4. Final status evaluation
        now = datetime.now(timezone.utc)
        duration_ms = max(int((now - job.started_at).total_seconds() * 1000), 0)
        job.completed_at = now
        job.duration_ms = duration_ms
        job.tables_scanned = total_tables_scanned
        job.columns_scanned = total_columns_scanned
        job.tables_created = total_tables_upserted
        job.tables_deleted = total_tables_deleted
        job.changes_detected = total_versions_created
        job.error_trace_jsonb = schema_errors

        if failed_schemas:
            if total_tables_scanned > 0 or (len(extractions) > len(failed_schemas)):
                job.status = SyncJobStatus.PARTIAL_FAILURE.value
                job.error_message = f"Partial failure: extraction failed for schemas: {', '.join(failed_schemas)}"
            else:
                job.status = SyncJobStatus.FAILED.value
                job.error_message = f"Failed to extract any schemas: {', '.join(failed_schemas)}"
        else:
            job.status = SyncJobStatus.SUCCESS.value
            job.error_message = None

        is_success = (job.status == SyncJobStatus.SUCCESS.value)
        await circuit_breaker_manager.record_job_outcome(
            sess,
            conn_uuid,
            success=is_success,
            error_message=job.error_message,
        )

        await sess.commit()
        await sess.refresh(job)

        return {
            "job_id": str(job.id),
            "connection_id": str(conn.id),
            "status": job.status,
            "duration_ms": duration_ms,
            "tables_scanned": total_tables_scanned,
            "columns_scanned": total_columns_scanned,
            "tables_upserted": total_tables_upserted,
            "tables_deleted": total_tables_deleted,
            "changes_detected": total_versions_created,
            "failed_schemas": failed_schemas,
            "error_message": job.error_message,
        }

    if session is not None:
        return await _execute(session)
    else:
        async with get_session_context() as sess:
            return await _execute(sess)


test_connection_sync.__test__ = False

# ==========================================
# Celery Task Definitions
# ==========================================

@celery_app.task(
    name="backend.src.services.workers.tasks.execute_metadata_sync_task",
    acks_late=True,
    bind=True,
)
def execute_metadata_sync_task(self, connection_id: str, job_id: str) -> Dict[str, Any]:
    """Background worker task for scheduled and automatic bulk metadata synchronization."""
    logger.info("Executing scheduled metadata sync for connection %s (job %s)", connection_id, job_id)
    return _run_coroutine(execute_metadata_sync(connection_id, job_id))


@celery_app.task(
    name="backend.src.services.workers.tasks.manual_metadata_sync_task",
    acks_late=True,
    bind=True,
)
def manual_metadata_sync_task(self, connection_id: str, job_id: str) -> Dict[str, Any]:
    """High-priority worker task for interactive admin manual metadata synchronization."""
    logger.info("Executing manual metadata sync for connection %s (job %s)", connection_id, job_id)
    return _run_coroutine(execute_metadata_sync(connection_id, job_id))


def probe_connection_task_fn(self, connection_id: str) -> Dict[str, Any]:
    """Fast worker task for testing database connectivity and credentials."""
    logger.info("Testing connection for connection %s", connection_id)
    return _run_coroutine(test_connection_sync(connection_id))


probe_connection_task_fn.__test__ = False

test_connection_task = celery_app.task(
    name="backend.src.services.workers.tasks.test_connection_task",
    acks_late=True,
    bind=True,
)(probe_connection_task_fn)
test_connection_task.__test__ = False
