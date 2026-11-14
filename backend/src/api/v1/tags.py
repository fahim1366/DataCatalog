from __future__ import annotations

import logging
from typing import List, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
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
from backend.src.schemas.catalog import BulkTagRequest, BulkTagResponse, TagItem
from backend.src.services.audit.audit_service import record_audit_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tags", tags=["Tags"])


class TagCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    color: str = Field(..., min_length=4, max_length=20)
    description: Optional[str] = None


class TagUpdateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    color: str = Field(..., min_length=4, max_length=20)
    description: Optional[str] = None


class TagDetailResponse(BaseModel):
    id: uuid.UUID
    name: str
    color: str
    description: Optional[str] = None
    assignment_count: int = 0

    model_config = ConfigDict(from_attributes=True)


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


@router.get("", response_model=List[TagDetailResponse], summary="List all taxonomy tags")
async def list_tags(
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> List[TagDetailResponse]:
    """Retrieves all taxonomy tags with active assignment counts."""
    stmt = (
        select(Tag, func.count(TagAssignment.id).label("assign_count"))
        .outerjoin(TagAssignment, TagAssignment.tag_id == Tag.id)
        .group_by(Tag.id)
        .order_by(Tag.name.asc())
    )
    res = await db.execute(stmt)
    rows = res.all()
    return [
        TagDetailResponse(
            id=tag.id,
            name=tag.name,
            color=tag.color,
            description=tag.description,
            assignment_count=acount,
        )
        for tag, acount in rows
    ]


@router.post("", response_model=TagDetailResponse, status_code=status.HTTP_201_CREATED, summary="Create tag")
async def create_tag(
    payload: TagCreateRequest,
    user_ctx: UserContext = Depends(require_manage_tags_permission),
    db: AsyncSession = Depends(get_db_session),
) -> TagDetailResponse:
    # Check unique name
    stmt = select(Tag).where(Tag.name == payload.name)
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Tag '{payload.name}' already exists")

    new_tag = Tag(
        id=uuid.uuid4(),
        name=payload.name,
        color=payload.color,
        description=payload.description,
    )
    db.add(new_tag)
    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user.id,
        action="CREATE_TAG",
        entity_type=EntityType.SCHEMA,
        entity_id=new_tag.id,
        diff_jsonb={"name": new_tag.name, "color": new_tag.color},
        flush=True,
    )
    await db.flush()

    return TagDetailResponse(
        id=new_tag.id,
        name=new_tag.name,
        color=new_tag.color,
        description=new_tag.description,
        assignment_count=0,
    )


@router.put("/{tag_id}", response_model=TagDetailResponse, summary="Update tag")
async def update_tag(
    tag_id: uuid.UUID,
    payload: TagUpdateRequest,
    user_ctx: UserContext = Depends(require_manage_tags_permission),
    db: AsyncSession = Depends(get_db_session),
) -> TagDetailResponse:
    tag = await db.get(Tag, tag_id)
    if not tag:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found")

    old_name = tag.name
    tag.name = payload.name
    tag.color = payload.color
    tag.description = payload.description

    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user.id,
        action="UPDATE_TAG",
        entity_type=EntityType.SCHEMA,
        entity_id=tag.id,
        diff_jsonb={"old_name": old_name, "new_name": tag.name, "color": tag.color},
        flush=True,
    )
    await db.flush()

    # Get assignment count
    ac_stmt = select(func.count(TagAssignment.id)).where(TagAssignment.tag_id == tag.id)
    ac = (await db.execute(ac_stmt)).scalar() or 0

    return TagDetailResponse(
        id=tag.id,
        name=tag.name,
        color=tag.color,
        description=tag.description,
        assignment_count=ac,
    )


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete tag")
async def delete_tag(
    tag_id: uuid.UUID,
    user_ctx: UserContext = Depends(require_manage_tags_permission),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    tag = await db.get(Tag, tag_id)
    if not tag:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found")

    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user.id,
        action="DELETE_TAG",
        entity_type=EntityType.SCHEMA,
        entity_id=tag.id,
        diff_jsonb={"deleted_name": tag.name},
        flush=True,
    )
    await db.delete(tag)
    await db.flush()


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
