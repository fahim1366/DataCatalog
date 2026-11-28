from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.api.dependencies.rbac import UserContext, build_table_rbac_filter, get_current_user
from backend.src.core.database import get_db_session
from backend.src.models.catalog import CatalogTable, DatabaseSchema, LifecycleStatus
from backend.src.models.connections import DatabaseConnection
from backend.src.models.rbac import PermissionAction
from backend.src.models.sync_jobs import SyncJob
from backend.src.models.versioning import TableVersion
from backend.src.schemas.versioning import (
    DatabaseSnapshotPoint,
    DatabaseTableDiffItem,
    DatabaseVersionDiffResponse,
    ModifiedColumnItem,
    TableVersionItem,
    VersionDiffResponse,
)
from backend.src.services.catalog.version_service import version_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Versioning"])


@router.get(
    "/tables/{table_id}/versions",
    response_model=List[TableVersionItem],
    summary="List all historical snapshot versions for a table",
)
async def list_table_versions(
    table_id: uuid.UUID,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> List[TableVersionItem]:
    """
    Returns the complete chronological history of immutable snapshot versions for a table.
    """
    stmt = select(CatalogTable).where(CatalogTable.id == table_id)
    result = await db.execute(stmt)
    table = result.scalar_one_or_none()
    if not table:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Table {table_id} not found",
        )

    versions = await version_service.get_versions(db, table_id)
    return [
        TableVersionItem(
            id=v.id,
            table_id=v.table_id,
            version_num=v.version_num,
            created_by_username=v.creator.username if v.creator else None,
            created_at=v.created_at,
        )
        for v in versions
    ]


@router.get(
    "/tables/{table_id}/versions/compare",
    response_model=VersionDiffResponse,
    summary="Compare two schema snapshot versions side-by-side",
)
async def compare_table_versions(
    table_id: uuid.UUID,
    v1: int = Query(..., ge=1, description="First version number to compare"),
    v2: int = Query(..., ge=1, description="Second version number to compare"),
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> VersionDiffResponse:
    """
    Computes a side-by-side structural delta between two versions of a table,
    identifying added columns, removed columns, and modified column attributes.
    """
    try:
        diff = await version_service.compare_versions(
            session=db,
            table_id=table_id,
            v1_num=v1,
            v2_num=v2,
        )
        return VersionDiffResponse(**diff)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.get(
    "/connections/{connection_id}/versions",
    response_model=List[DatabaseSnapshotPoint],
    summary="List chronological schema snapshot points for a database connection",
)
async def list_connection_versions(
    connection_id: uuid.UUID,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> List[DatabaseSnapshotPoint]:
    """
    Returns chronological snapshot points for a database connection by combining
    completed SyncJob runs and distinct authorized TableVersion timestamps.
    """
    conn_stmt = select(DatabaseConnection).where(DatabaseConnection.id == connection_id)
    conn = (await db.execute(conn_stmt)).scalar_one_or_none()
    if not conn:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connection {connection_id} not found",
        )

    points: List[DatabaseSnapshotPoint] = []
    sync_timestamps: List[datetime] = []

    job_stmt = (
        select(SyncJob)
        .where(
            SyncJob.connection_id == connection_id,
            SyncJob.status.in_(["SUCCESS", "PARTIAL_FAILURE"]),
            SyncJob.completed_at.is_not(None),
        )
        .order_by(SyncJob.completed_at.asc())
    )
    jobs = list((await db.execute(job_stmt)).scalars().all())
    for job in jobs:
        if job.completed_at is None:
            continue
        sync_timestamps.append(job.completed_at)
        points.append(
            DatabaseSnapshotPoint(
                timestamp=job.completed_at,
                label=f"همگام‌سازی ({job.triggered_by})",
                source="SYNC_JOB",
                sync_job_id=job.id,
                tables_count=job.tables_scanned or 0,
                changes_count=job.changes_detected or 0,
            )
        )

    tv_stmt = (
        select(TableVersion.created_at, TableVersion.table_id)
        .join(CatalogTable, CatalogTable.id == TableVersion.table_id)
        .join(DatabaseSchema, DatabaseSchema.id == CatalogTable.schema_id)
        .where(DatabaseSchema.connection_id == connection_id)
    )
    rbac_filter = build_table_rbac_filter(user_ctx, PermissionAction.VIEW_METADATA)
    tv_stmt = rbac_filter(tv_stmt).order_by(TableVersion.created_at.asc())
    tv_rows = (await db.execute(tv_stmt)).all()

    grouped_tv: Dict[datetime, set] = {}
    for created_at, tbl_id in tv_rows:
        if created_at is None:
            continue
        bucket = created_at.replace(microsecond=0)
        grouped_tv.setdefault(bucket, set()).add(tbl_id)

    for ts, tbl_ids in grouped_tv.items():
        if any(abs((ts - st).total_seconds()) <= 5 for st in sync_timestamps):
            continue
        points.append(
            DatabaseSnapshotPoint(
                timestamp=ts,
                label="اسنپ‌شات اسکیما",
                source="SNAPSHOT",
                sync_job_id=None,
                tables_count=len(tbl_ids),
                changes_count=len(tbl_ids),
            )
        )

    points.sort(key=lambda p: p.timestamp)
    return points


@router.get(
    "/connections/{connection_id}/versions/compare",
    response_model=DatabaseVersionDiffResponse,
    summary="Compare database-wide schema versions between two timestamps",
)
async def compare_connection_versions(
    connection_id: uuid.UUID,
    v1_ts: datetime = Query(..., description="Baseline ISO timestamp"),
    v2_ts: datetime = Query(..., description="Target ISO timestamp"),
    schema_name: Optional[str] = Query(None, description="Optional schema name filter"),
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> DatabaseVersionDiffResponse:
    """
    Computes a database-wide schema diff between two timestamps for all tables
    authorized under VIEW_METADATA for the current user.
    """
    conn_stmt = select(DatabaseConnection).where(DatabaseConnection.id == connection_id)
    conn = (await db.execute(conn_stmt)).scalar_one_or_none()
    if not conn:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connection {connection_id} not found",
        )

    table_stmt = (
        select(CatalogTable, DatabaseSchema.name.label("schema_name"))
        .join(DatabaseSchema, DatabaseSchema.id == CatalogTable.schema_id)
        .where(DatabaseSchema.connection_id == connection_id)
    )
    if schema_name:
        table_stmt = table_stmt.where(DatabaseSchema.name == schema_name)
    rbac_filter = build_table_rbac_filter(user_ctx, PermissionAction.VIEW_METADATA)
    table_stmt = rbac_filter(table_stmt).order_by(DatabaseSchema.name.asc(), CatalogTable.name.asc())
    table_rows = (await db.execute(table_stmt)).all()

    table_ids = [tbl.id for tbl, _ in table_rows]
    versions_by_table: Dict[uuid.UUID, List[TableVersion]] = {}
    if table_ids:
        max_ts = max(v1_ts, v2_ts)
        tv_stmt = (
            select(TableVersion)
            .where(
                TableVersion.table_id.in_(table_ids),
                TableVersion.created_at <= max_ts,
            )
            .order_by(
                TableVersion.table_id.asc(),
                TableVersion.created_at.desc(),
                TableVersion.version_num.desc(),
            )
        )
        for tv in (await db.execute(tv_stmt)).scalars().all():
            versions_by_table.setdefault(tv.table_id, []).append(tv)

    added_tables_count = 0
    dropped_tables_count = 0
    modified_tables_count = 0
    unchanged_tables_count = 0
    total_column_changes = 0
    diff_items: List[DatabaseTableDiffItem] = []

    for tbl, s_name in table_rows:
        t_versions = versions_by_table.get(tbl.id, [])
        snap1 = next((v for v in t_versions if v.created_at <= v1_ts), None)
        snap2 = next((v for v in t_versions if v.created_at <= v2_ts), None)

        snap1_active = snap1 is not None and snap1.snapshot_jsonb.get("status") != "DELETED"
        snap2_active = snap2 is not None and snap2.snapshot_jsonb.get("status") != "DELETED"
        if (
            snap2_active
            and tbl.status == LifecycleStatus.DELETED
            and tbl.updated_at is not None
            and v1_ts < tbl.updated_at <= v2_ts
        ):
            snap2_active = False

        if not snap1_active and not snap2_active:
            continue

        if not snap1_active and snap2_active:
            added_tables_count += 1
            cols = snap2.snapshot_jsonb.get("columns", [])
            total_column_changes += len(cols)
            diff_items.append(
                DatabaseTableDiffItem(
                    table_id=tbl.id,
                    schema_name=s_name,
                    table_name=tbl.name,
                    change_status="ADDED",
                    v1_version_num=None,
                    v2_version_num=snap2.version_num,
                    added_columns=cols,
                )
            )
        elif snap1_active and not snap2_active:
            dropped_tables_count += 1
            cols = snap1.snapshot_jsonb.get("columns", [])
            total_column_changes += len(cols)
            diff_items.append(
                DatabaseTableDiffItem(
                    table_id=tbl.id,
                    schema_name=s_name,
                    table_name=tbl.name,
                    change_status="DROPPED",
                    v1_version_num=snap1.version_num,
                    v2_version_num=None,
                    removed_columns=cols,
                )
            )
        else:
            assert snap1 is not None and snap2 is not None
            diff = version_service.compute_diff(snap1.snapshot_jsonb, snap2.snapshot_jsonb)
            added_c = diff.get("added_columns", [])
            removed_c = diff.get("removed_columns", [])
            mod_c = diff.get("modified_columns", [])
            if added_c or removed_c or mod_c:
                modified_tables_count += 1
                total_column_changes += len(added_c) + len(removed_c) + len(mod_c)
                diff_items.append(
                    DatabaseTableDiffItem(
                        table_id=tbl.id,
                        schema_name=s_name,
                        table_name=tbl.name,
                        change_status="MODIFIED",
                        v1_version_num=snap1.version_num,
                        v2_version_num=snap2.version_num,
                        added_columns=added_c,
                        removed_columns=removed_c,
                        modified_columns=[ModifiedColumnItem(**m) for m in mod_c],
                    )
                )
            else:
                unchanged_tables_count += 1

    return DatabaseVersionDiffResponse(
        connection_id=connection_id,
        v1_ts=v1_ts,
        v2_ts=v2_ts,
        added_tables_count=added_tables_count,
        dropped_tables_count=dropped_tables_count,
        modified_tables_count=modified_tables_count,
        unchanged_tables_count=unchanged_tables_count,
        total_column_changes=total_column_changes,
        tables=diff_items,
    )

