from __future__ import annotations

import logging
import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.api.dependencies.rbac import UserContext, get_current_user
from backend.src.core.database import get_db_session
from backend.src.models.catalog import Tag, TagAssignment
from backend.src.models.rbac import (
    EntityType,
    PermissionAction,
    PermissionEffect,
    PermissionRule,
)
from backend.src.models.versioning import SchemaChangeLog, SchemaChangeType
from backend.src.schemas.catalog import BulkTagRequest, BulkTagResponse
from backend.src.services.audit.audit_service import record_audit_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tags", tags=["Tags"])


async def require_manage_tags_permission(
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> UserContext:
    """Enforces that the user has MANAGE_TAGS permission or is superadmin."""
    if user_ctx.is_superadmin:
        return user_ctx

    if not user_ctx.role_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: User lacks tag management permissions.",
        )

    # 1. Check explicit DENY for MANAGE_TAGS
    deny_stmt = select(1).where(
        PermissionRule.role_id.in_(user_ctx.role_ids),
        PermissionRule.action == PermissionAction.MANAGE_TAGS,
        PermissionRule.effect == PermissionEffect.DENY,
    )
    deny_res = await db.execute(deny_stmt)
    if deny_res.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: User explicitly denied tag management access.",
        )

    # 2. Check explicit ALLOW for MANAGE_TAGS
    allow_stmt = select(1).where(
        PermissionRule.role_id.in_(user_ctx.role_ids),
        PermissionRule.action == PermissionAction.MANAGE_TAGS,
        PermissionRule.effect == PermissionEffect.ALLOW,
    )
    allow_res = await db.execute(allow_stmt)
    if allow_res.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: User lacks MANAGE_TAGS permission.",
        )

    return user_ctx


@router.post(
    "/bulk-assign",
    response_model=BulkTagResponse,
    summary="Bulk assign or remove tags across multiple entities",
)
async def bulk_tag_entities(
    payload: BulkTagRequest,
    user_ctx: UserContext = Depends(require_manage_tags_permission),
    db: AsyncSession = Depends(get_db_session),
) -> BulkTagResponse:
    """
    Atomically assigns or removes tags across multiple entities (DATABASE, SCHEMA, TABLE, COLUMN).
    Records MANAGE_TAGS immutable AuditLog entries and SchemaChangeLog entries.
    """
    if not payload.tag_ids or not payload.targets:
        return BulkTagResponse(success=True, affected_count=0)

    # Verify requested tags exist
    tag_stmt = select(Tag).where(Tag.id.in_(payload.tag_ids))
    tag_res = await db.execute(tag_stmt)
    existing_tags = {tag.id: tag for tag in tag_res.scalars().all()}
    if len(existing_tags) != len(set(payload.tag_ids)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="One or more specified tags were not found.",
        )

    affected_count = 0

    if payload.action == "ASSIGN":
        for target in payload.targets:
            target_entity_type = EntityType(target.entity_type)
            for tag_id in payload.tag_ids:
                tag = existing_tags[tag_id]
                # Check if assignment already exists
                check_stmt = select(TagAssignment).where(
                    TagAssignment.tag_id == tag_id,
                    TagAssignment.entity_type == target_entity_type,
                    TagAssignment.entity_id == target.entity_id,
                )
                check_res = await db.execute(check_stmt)
                if check_res.scalar_one_or_none() is None:
                    new_assignment = TagAssignment(
                        id=uuid.uuid4(),
                        tag_id=tag_id,
                        entity_type=target_entity_type,
                        entity_id=target.entity_id,
                        assigned_by=user_ctx.user_id,
                    )
                    db.add(new_assignment)
                    affected_count += 1

                    # Record SchemaChangeLog if entity is TABLE
                    if target_entity_type == EntityType.TABLE:
                        db.add(
                            SchemaChangeLog(
                                id=uuid.uuid4(),
                                table_id=target.entity_id,
                                change_type=SchemaChangeType.TAG_ASSIGNED,
                                new_value={"tag_id": str(tag_id), "tag_name": tag.name},
                                actor_id=user_ctx.user_id,
                            )
                        )

                    # Record AuditLog
                    await record_audit_event(
                        session=db,
                        actor_username=user_ctx.user.username,
                        actor_id=user_ctx.user_id,
                        action="MANAGE_TAGS",
                        entity_type=target_entity_type,
                        entity_id=target.entity_id,
                        diff_jsonb={
                            "operation": "ASSIGN",
                            "tag_id": str(tag_id),
                            "tag_name": tag.name,
                        },
                        flush=True,
                    )

    elif payload.action == "REMOVE":
        for target in payload.targets:
            target_entity_type = EntityType(target.entity_type)
            for tag_id in payload.tag_ids:
                tag = existing_tags[tag_id]
                stmt = select(TagAssignment).where(
                    TagAssignment.tag_id == tag_id,
                    TagAssignment.entity_type == target_entity_type,
                    TagAssignment.entity_id == target.entity_id,
                )
                res = await db.execute(stmt)
                existing_assignment = res.scalar_one_or_none()
                if existing_assignment is not None:
                    await db.delete(existing_assignment)
                    affected_count += 1

                    # Record SchemaChangeLog if entity is TABLE
                    if target_entity_type == EntityType.TABLE:
                        db.add(
                            SchemaChangeLog(
                                id=uuid.uuid4(),
                                table_id=target.entity_id,
                                change_type=SchemaChangeType.TAG_REMOVED,
                                old_value={"tag_id": str(tag_id), "tag_name": tag.name},
                                actor_id=user_ctx.user_id,
                            )
                        )

                    # Record AuditLog
                    await record_audit_event(
                        session=db,
                        actor_username=user_ctx.user.username,
                        actor_id=user_ctx.user_id,
                        action="MANAGE_TAGS",
                        entity_type=target_entity_type,
                        entity_id=target.entity_id,
                        diff_jsonb={
                            "operation": "REMOVE",
                            "tag_id": str(tag_id),
                            "tag_name": tag.name,
                        },
                        flush=True,
                    )

    await db.flush()
    return BulkTagResponse(success=True, affected_count=affected_count)

