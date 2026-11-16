from __future__ import annotations

import enum
import logging
from typing import Optional, Union
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.models.catalog import CatalogColumn, CatalogTable
from backend.src.models.rbac import EntityType
from backend.src.models.versioning import SchemaChangeLog, SchemaChangeType
from backend.src.services.audit.audit_service import record_audit_event

logger = logging.getLogger(__name__)


class DriftResolutionAction(str, enum.Enum):
    """Actions available for resolving description drift conflicts."""

    RETAIN_APPROVED = "RETAIN_APPROVED"
    OVERWRITE_WITH_SOURCE = "OVERWRITE_WITH_SOURCE"
    CUSTOM_MERGE = "CUSTOM_MERGE"


class DriftService:
    """
    Service responsible for detecting divergence between database native comments and
    human-curated catalog descriptions, and resolving conflicts via three supported workflows:
    - RETAIN_APPROVED: Keeps the curated approved description intact and clears the drift flag.
    - OVERWRITE_WITH_SOURCE: Overwrites approved description with the native DB comment.
    - CUSTOM_MERGE: Blends or replaces descriptions with user-provided merged text.
    """

    async def detect_and_flag_drift(
        self,
        session: AsyncSession,
        entity: Union[CatalogTable, CatalogColumn],
        extracted_comment: Optional[str],
        actor_id: Optional[uuid.UUID] = None,
    ) -> bool:
        """
        Detects if extracted database comment diverges from both current source_description
        and existing approved_description. If drift detected, flags entity and creates
        SchemaChangeLog(DESCRIPTION_DRIFT) without incrementing TableVersion.
        """
        has_curated_desc = bool(
            entity.approved_description and entity.approved_description.strip()
        )
        source_changed = extracted_comment != entity.source_description
        differs_from_curated = (
            extracted_comment != entity.approved_description
            if has_curated_desc
            else False
        )

        if has_curated_desc and source_changed and differs_from_curated:
            entity.description_drift_detected = True
            entity.updated_at = func.clock_timestamp()

            table_id = (
                entity.id if isinstance(entity, CatalogTable) else entity.table_id
            )
            col_name = entity.name if isinstance(entity, CatalogColumn) else None

            change_log = SchemaChangeLog(
                id=uuid.uuid4(),
                table_id=table_id,
                version_id=None,  # Drift does not increment structural TableVersion
                change_type=SchemaChangeType.DESCRIPTION_DRIFT,
                column_name=col_name,
                old_value={
                    "source_description": entity.source_description,
                    "approved_description": entity.approved_description,
                },
                new_value={
                    "source_description": extracted_comment,
                },
                actor_id=actor_id,
            )
            session.add(change_log)
            entity.source_description = extracted_comment
            await session.flush()
            return True

        # If no drift, simply update source_description to stay in sync with DB
        if source_changed:
            entity.source_description = extracted_comment
            entity.updated_at = func.clock_timestamp()
            await session.flush()

        return False

    async def resolve_table_drift(
        self,
        session: AsyncSession,
        table_id: uuid.UUID,
        resolution_action: Union[str, DriftResolutionAction],
        custom_text: Optional[str] = None,
        actor_id: Optional[uuid.UUID] = None,
        actor_username: str = "system",
    ) -> CatalogTable:
        """Resolves description drift on a catalog table."""
        stmt = select(CatalogTable).where(CatalogTable.id == table_id)
        res = await session.execute(stmt)
        table = res.scalar_one_or_none()
        if not table:
            raise ValueError(f"Table with ID '{table_id}' not found.")

        action = (
            resolution_action.value
            if hasattr(resolution_action, "value")
            else str(resolution_action)
        )

        if action == DriftResolutionAction.RETAIN_APPROVED.value:
            # Retain approved description as is, clear drift flag
            table.description_drift_detected = False
        elif action == DriftResolutionAction.OVERWRITE_WITH_SOURCE.value:
            # Overwrite approved description with source comment
            table.approved_description = table.source_description
            table.description_source = "SOURCE_DB"
            table.curated_by_id = actor_id
            table.curated_by_username = actor_username
            table.curated_at = func.clock_timestamp()
            table.description_drift_detected = False
        elif action == DriftResolutionAction.CUSTOM_MERGE.value:
            if custom_text is None:
                raise ValueError("custom_text must be provided for CUSTOM_MERGE action.")
            table.approved_description = custom_text
            table.description_source = "HUMAN"
            table.curated_by_id = actor_id
            table.curated_by_username = actor_username
            table.curated_at = func.clock_timestamp()
            table.description_drift_detected = False
        else:
            raise ValueError(f"Unknown drift resolution action: {action}")

        table.updated_at = func.clock_timestamp()

        # Audit Log Entry
        await record_audit_event(
            session=session,
            actor_username=actor_username,
            action="RESOLVE_TABLE_DESCRIPTION_DRIFT",
            entity_type=EntityType.TABLE,
            entity_id=table.id,
            actor_id=actor_id,
            diff_jsonb={
                "action": action,
                "approved_description": table.approved_description,
                "source_description": table.source_description,
            },
            flush=True,
        )

        return table

    async def resolve_column_drift(
        self,
        session: AsyncSession,
        column_id: uuid.UUID,
        resolution_action: Union[str, DriftResolutionAction],
        custom_text: Optional[str] = None,
        actor_id: Optional[uuid.UUID] = None,
        actor_username: str = "system",
    ) -> CatalogColumn:
        """Resolves description drift on a catalog column."""
        stmt = select(CatalogColumn).where(CatalogColumn.id == column_id)
        res = await session.execute(stmt)
        column = res.scalar_one_or_none()
        if not column:
            raise ValueError(f"Column with ID '{column_id}' not found.")

        action = (
            resolution_action.value
            if hasattr(resolution_action, "value")
            else str(resolution_action)
        )

        if action == DriftResolutionAction.RETAIN_APPROVED.value:
            column.description_drift_detected = False
        elif action == DriftResolutionAction.OVERWRITE_WITH_SOURCE.value:
            column.approved_description = column.source_description
            column.description_source = "SOURCE_DB"
            column.curated_by_id = actor_id
            column.curated_by_username = actor_username
            column.curated_at = func.clock_timestamp()
            column.description_drift_detected = False
        elif action == DriftResolutionAction.CUSTOM_MERGE.value:
            if custom_text is None:
                raise ValueError("custom_text must be provided for CUSTOM_MERGE action.")
            column.approved_description = custom_text
            column.description_source = "HUMAN"
            column.curated_by_id = actor_id
            column.curated_by_username = actor_username
            column.curated_at = func.clock_timestamp()
            column.description_drift_detected = False
        else:
            raise ValueError(f"Unknown drift resolution action: {action}")

        column.updated_at = func.clock_timestamp()

        # Audit Log Entry
        await record_audit_event(
            session=session,
            actor_username=actor_username,
            action="RESOLVE_COLUMN_DESCRIPTION_DRIFT",
            entity_type=EntityType.COLUMN,
            entity_id=column.id,
            actor_id=actor_id,
            diff_jsonb={
                "action": action,
                "approved_description": column.approved_description,
                "source_description": column.source_description,
            },
            flush=True,
        )

        return column


drift_service = DriftService()
