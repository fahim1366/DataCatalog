from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple
import uuid

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.src.models.catalog import CatalogColumn, CatalogTable, LifecycleStatus
from backend.src.models.identity import User
from backend.src.models.versioning import SchemaChangeLog, SchemaChangeType, TableVersion

logger = logging.getLogger(__name__)


class VersionService:
    """
    Enterprise Schema Snapshotting, Versioning, and Structural Diff Calculation Engine.
    Guarantees immutable historical snapshots, monotonic version incrementing,
    and granular column delta detection (added, dropped/removed, modified types).
    """

    async def create_snapshot(
        self, session: AsyncSession, table_id: uuid.UUID
    ) -> Dict[str, Any]:
        """
        Generates a complete, deterministic JSONB snapshot of a catalog table and its
        currently active columns.
        """
        table_stmt = (
            select(CatalogTable)
            .where(CatalogTable.id == table_id)
            .execution_options(populate_existing=True)
        )
        table_result = await session.execute(table_stmt)
        table = table_result.scalar_one_or_none()
        if not table:
            raise ValueError(f"Table with ID {table_id} does not exist.")

        col_stmt = (
            select(CatalogColumn)
            .where(
                CatalogColumn.table_id == table_id,
                CatalogColumn.status != LifecycleStatus.DELETED,
            )
            .execution_options(populate_existing=True)
            .order_by(CatalogColumn.ordinal_position.asc())
        )
        col_result = await session.execute(col_stmt)
        columns = list(col_result.scalars().all())

        table_type_str = (
            table.table_type.value
            if hasattr(table.table_type, "value")
            else str(table.table_type)
        )
        status_str = (
            table.status.value
            if hasattr(table.status, "value")
            else str(table.status)
        )

        columns_data = []
        for col in columns:
            cls_level = (
                col.classification_level.value
                if hasattr(col.classification_level, "value")
                else str(col.classification_level)
            )
            col_status = (
                col.status.value
                if hasattr(col.status, "value")
                else str(col.status)
            )
            columns_data.append(
                {
                    "id": str(col.id),
                    "name": col.name,
                    "ordinal_position": col.ordinal_position,
                    "data_type": col.data_type,
                    "raw_data_type": col.raw_data_type,
                    "is_nullable": col.is_nullable,
                    "is_primary_key": col.is_primary_key,
                    "is_foreign_key": col.is_foreign_key,
                    "default_value": col.default_value,
                    "source_description": col.source_description,
                    "classification_level": cls_level,
                    "status": col_status,
                }
            )

        return {
            "table_id": str(table.id),
            "name": table.name,
            "table_type": table_type_str,
            "row_count_estimate": table.row_count_estimate,
            "size_bytes": table.size_bytes,
            "source_description": table.source_description,
            "owner": table.owner,
            "status": status_str,
            "columns": columns_data,
        }

    def compute_column_deltas(
        self, v1_snapshot: Dict[str, Any], v2_snapshot: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Computes granular structural column deltas between two snapshots.
        Detects COLUMN_ADDED, COLUMN_REMOVED, and TYPE_CHANGED.
        """
        v1_cols = {col["name"]: col for col in v1_snapshot.get("columns", [])}
        v2_cols = {col["name"]: col for col in v2_snapshot.get("columns", [])}

        deltas: List[Dict[str, Any]] = []

        # 1. Added columns (present in v2, absent in v1)
        for name, col in v2_cols.items():
            if name not in v1_cols:
                deltas.append(
                    {
                        "change_type": "COLUMN_ADDED",
                        "column_name": name,
                        "old_value": None,
                        "new_value": col,
                    }
                )

        # 2. Removed columns (present in v1, absent in v2)
        for name, col in v1_cols.items():
            if name not in v2_cols:
                deltas.append(
                    {
                        "change_type": "COLUMN_REMOVED",
                        "column_name": name,
                        "old_value": col,
                        "new_value": None,
                    }
                )

        # 3. Modified columns (present in both)
        for name, col1 in v1_cols.items():
            if name in v2_cols:
                col2 = v2_cols[name]
                # Check data type changes
                if (
                    col1.get("data_type") != col2.get("data_type")
                    or col1.get("raw_data_type") != col2.get("raw_data_type")
                ):
                    deltas.append(
                        {
                            "change_type": "TYPE_CHANGED",
                            "column_name": name,
                            "old_value": {
                                "data_type": col1.get("data_type"),
                                "raw_data_type": col1.get("raw_data_type"),
                            },
                            "new_value": {
                                "data_type": col2.get("data_type"),
                                "raw_data_type": col2.get("raw_data_type"),
                            },
                        }
                    )
                # Check nullability changes
                elif col1.get("is_nullable") != col2.get("is_nullable"):
                    deltas.append(
                        {
                            "change_type": "NULLABILITY_CHANGED",
                            "column_name": name,
                            "old_value": {"is_nullable": col1.get("is_nullable")},
                            "new_value": {"is_nullable": col2.get("is_nullable")},
                        }
                    )

        return deltas

    def compute_diff(
        self, v1_snapshot: Dict[str, Any], v2_snapshot: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Generates side-by-side comparison payload conforming to OpenAPI VersionDiffResponse.
        Categorizes differences into added_columns, removed_columns, and modified_columns.
        """
        v1_cols = {col["name"]: col for col in v1_snapshot.get("columns", [])}
        v2_cols = {col["name"]: col for col in v2_snapshot.get("columns", [])}

        added = [col for name, col in v2_cols.items() if name not in v1_cols]
        removed = [col for name, col in v1_cols.items() if name not in v2_cols]
        modified = []

        for name, col1 in v1_cols.items():
            if name in v2_cols:
                col2 = v2_cols[name]
                diffs_v1 = {}
                diffs_v2 = {}
                # Compare attributes
                for attr in [
                    "data_type",
                    "raw_data_type",
                    "is_nullable",
                    "is_primary_key",
                    "is_foreign_key",
                    "default_value",
                    "classification_level",
                ]:
                    if col1.get(attr) != col2.get(attr):
                        diffs_v1[attr] = col1.get(attr)
                        diffs_v2[attr] = col2.get(attr)

                if diffs_v1 or diffs_v2:
                    modified.append(
                        {
                            "column_name": name,
                            "v1_attributes": diffs_v1,
                            "v2_attributes": diffs_v2,
                        }
                    )

        return {
            "added_columns": added,
            "removed_columns": removed,
            "modified_columns": modified,
        }

    async def get_latest_version(
        self, session: AsyncSession, table_id: uuid.UUID
    ) -> Optional[TableVersion]:
        """Returns the most recent TableVersion record for a table."""
        stmt = (
            select(TableVersion)
            .where(TableVersion.table_id == table_id)
            .order_by(desc(TableVersion.version_num))
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def capture_version(
        self,
        session: AsyncSession,
        table_id: uuid.UUID,
        actor_id: Optional[uuid.UUID] = None,
        actor_username: Optional[str] = None,
    ) -> TableVersion:
        """
        Unconditionally captures a new TableVersion snapshot, incrementing version_num
        and recording SchemaChangeLog records if a previous version existed.
        """
        latest = await self.get_latest_version(session, table_id)
        next_version_num = (latest.version_num + 1) if latest else 1

        snapshot = await self.create_snapshot(session, table_id)

        version = TableVersion(
            id=uuid.uuid4(),
            table_id=table_id,
            version_num=next_version_num,
            snapshot_jsonb=snapshot,
            created_by=actor_id,
        )
        session.add(version)
        await session.flush()

        # If previous version exists, log the schema changes
        if latest:
            deltas = self.compute_column_deltas(latest.snapshot_jsonb, snapshot)
            for delta in deltas:
                raw_type = delta["change_type"]
                enum_type = (
                    SchemaChangeType.COLUMN_DROPPED
                    if raw_type == "COLUMN_REMOVED"
                    else SchemaChangeType(raw_type)
                )
                log = SchemaChangeLog(
                    id=uuid.uuid4(),
                    table_id=table_id,
                    version_id=version.id,
                    change_type=enum_type,
                    column_name=delta["column_name"],
                    old_value=delta["old_value"],
                    new_value=delta["new_value"],
                    actor_id=actor_id,
                )
                session.add(log)
            await session.flush()

        return version

    async def capture_version_if_changed(
        self,
        session: AsyncSession,
        table_id: uuid.UUID,
        actor_id: Optional[uuid.UUID] = None,
        actor_username: Optional[str] = None,
    ) -> Optional[TableVersion]:
        """
        Captures a new TableVersion snapshot only if structural column differences exist
        compared to the latest recorded snapshot. If no changes exist, returns None.
        """
        latest = await self.get_latest_version(session, table_id)
        current_snapshot = await self.create_snapshot(session, table_id)

        if not latest:
            # First version
            version = TableVersion(
                id=uuid.uuid4(),
                table_id=table_id,
                version_num=1,
                snapshot_jsonb=current_snapshot,
                created_by=actor_id,
            )
            session.add(version)
            await session.flush()
            return version

        deltas = self.compute_column_deltas(
            latest.snapshot_jsonb, current_snapshot
        )
        if not deltas:
            # No structural schema changes
            return None

        next_version_num = latest.version_num + 1
        version = TableVersion(
            id=uuid.uuid4(),
            table_id=table_id,
            version_num=next_version_num,
            snapshot_jsonb=current_snapshot,
            created_by=actor_id,
        )
        session.add(version)
        await session.flush()

        for delta in deltas:
            raw_type = delta["change_type"]
            enum_type = (
                SchemaChangeType.COLUMN_DROPPED
                if raw_type == "COLUMN_REMOVED"
                else SchemaChangeType(raw_type)
            )
            log = SchemaChangeLog(
                id=uuid.uuid4(),
                table_id=table_id,
                version_id=version.id,
                change_type=enum_type,
                column_name=delta["column_name"],
                old_value=delta["old_value"],
                new_value=delta["new_value"],
                actor_id=actor_id,
            )
            session.add(log)

        await session.flush()
        return version

    async def get_versions(
        self, session: AsyncSession, table_id: uuid.UUID
    ) -> List[TableVersion]:
        """Returns all historical version snapshots for a table ordered by version_num."""
        stmt = (
            select(TableVersion)
            .options(selectinload(TableVersion.creator))
            .where(TableVersion.table_id == table_id)
            .order_by(TableVersion.version_num.asc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def compare_versions(
        self,
        session: AsyncSession,
        table_id: uuid.UUID,
        v1_num: int,
        v2_num: int,
    ) -> Dict[str, Any]:
        """
        Compares two specific schema snapshot versions of a table side-by-side.
        Raises ValueError if the table or requested version numbers are not found.
        """
        stmt_tbl = select(CatalogTable).where(CatalogTable.id == table_id)
        tbl_res = await session.execute(stmt_tbl)
        if not tbl_res.scalar_one_or_none():
            raise ValueError(f"Table {table_id} not found.")

        stmt = select(TableVersion).where(
            TableVersion.table_id == table_id,
            TableVersion.version_num.in_([v1_num, v2_num]),
        )
        result = await session.execute(stmt)
        versions_by_num = {v.version_num: v for v in result.scalars().all()}

        if v1_num not in versions_by_num:
            raise ValueError(f"Version {v1_num} not found for table {table_id}.")
        if v2_num not in versions_by_num:
            raise ValueError(f"Version {v2_num} not found for table {table_id}.")

        v1 = versions_by_num[v1_num]
        v2 = versions_by_num[v2_num]

        diff_payload = self.compute_diff(v1.snapshot_jsonb, v2.snapshot_jsonb)
        return {
            "table_id": table_id,
            "v1_num": v1_num,
            "v2_num": v2_num,
            "added_columns": diff_payload["added_columns"],
            "removed_columns": diff_payload["removed_columns"],
            "modified_columns": diff_payload["modified_columns"],
        }

    async def get_table_change_logs(
        self, session: AsyncSession, table_id: uuid.UUID
    ) -> List[SchemaChangeLog]:
        """Returns all recorded schema change log entries for a table."""
        stmt = (
            select(SchemaChangeLog)
            .where(SchemaChangeLog.table_id == table_id)
            .order_by(SchemaChangeLog.created_at.desc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())


version_service = VersionService()
