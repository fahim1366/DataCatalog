from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.api.dependencies.rbac import UserContext, get_current_user
from backend.src.core.database import get_db_session
from backend.src.models.identity import Role
from backend.src.models.rbac import (
    EntityType,
    PermissionAction,
    PermissionEffect,
    PermissionRule,
    ResourceType,
)
from backend.src.services.audit.audit_service import record_audit_event
from backend.src.services.rbac.category_service import CategoryService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/permissions", tags=["Permissions"])


class PermissionRuleCreateRequest(BaseModel):
    role_id: uuid.UUID
    resource_type: ResourceType
    resource_id: Optional[uuid.UUID] = None
    action: PermissionAction
    effect: PermissionEffect = PermissionEffect.ALLOW


class PermissionRuleResponse(BaseModel):
    id: uuid.UUID
    role_id: uuid.UUID
    resource_type: ResourceType
    resource_id: Optional[uuid.UUID] = None
    action: PermissionAction
    effect: PermissionEffect
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EffectivePermissionCheckResponse(BaseModel):
    entity_type: EntityType
    entity_id: uuid.UUID
    action: PermissionAction
    effect: PermissionEffect


@router.post(
    "",
    response_model=PermissionRuleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Grant a permission rule to a role",
)
async def grant_permission_rule(
    payload: PermissionRuleCreateRequest,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> PermissionRuleResponse:
    """
    Grants a fine-grained permission rule (ALLOW or DENY) to a role across
    Global, Database, Schema, Table, Column, or Category resources.
    Atomically records an immutable AuditLog event in the current transaction.
    """
    # Verify that target role exists
    role = await db.get(Role, payload.role_id)
    if not role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Role '{payload.role_id}' not found.",
        )

    rule = PermissionRule(
        id=uuid.uuid4(),
        role_id=payload.role_id,
        resource_type=payload.resource_type,
        resource_id=payload.resource_id,
        action=payload.action,
        effect=payload.effect,
    )
    db.add(rule)
    await db.flush()

    # Determine audit entity_type and entity_id
    if payload.resource_type in (
        ResourceType.DATABASE,
        ResourceType.SCHEMA,
        ResourceType.TABLE,
        ResourceType.COLUMN,
    ):
        audit_entity_type = EntityType(payload.resource_type.value)
    else:
        audit_entity_type = EntityType.DATABASE

    audit_entity_id = (
        payload.resource_id if payload.resource_id is not None else payload.role_id
    )

    # Atomically record audit log
    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user.id,
        action="GRANT_PERMISSION",
        entity_type=audit_entity_type,
        entity_id=audit_entity_id,
        diff_jsonb={
            "rule_id": str(rule.id),
            "role_id": str(rule.role_id),
            "resource_type": rule.resource_type.value,
            "resource_id": str(rule.resource_id) if rule.resource_id else None,
            "action": rule.action.value,
            "effect": rule.effect.value,
        },
        flush=True,
    )

    return PermissionRuleResponse.model_validate(rule)


@router.get(
    "",
    response_model=List[PermissionRuleResponse],
    summary="List and filter permission rules",
)
async def list_permissions(
    role_id: Optional[uuid.UUID] = Query(None, description="Filter by assigned role ID"),
    resource_type: Optional[ResourceType] = Query(None, description="Filter by resource type"),
    resource_id: Optional[uuid.UUID] = Query(None, description="Filter by resource ID"),
    action: Optional[PermissionAction] = Query(None, description="Filter by permission action"),
    effect: Optional[PermissionEffect] = Query(None, description="Filter by permission effect"),
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> List[PermissionRuleResponse]:
    """
    Retrieves and filters active permission rules.
    """
    stmt = select(PermissionRule)

    if role_id is not None:
        stmt = stmt.where(PermissionRule.role_id == role_id)
    if resource_type is not None:
        stmt = stmt.where(PermissionRule.resource_type == resource_type)
    if resource_id is not None:
        stmt = stmt.where(PermissionRule.resource_id == resource_id)
    if action is not None:
        stmt = stmt.where(PermissionRule.action == action)
    if effect is not None:
        stmt = stmt.where(PermissionRule.effect == effect)

    stmt = stmt.order_by(PermissionRule.created_at.desc())
    result = await db.execute(stmt)
    rules = result.scalars().all()
    return [PermissionRuleResponse.model_validate(r) for r in rules]


@router.get(
    "/roles/{role_id}",
    response_model=List[PermissionRuleResponse],
    summary="Get all permissions granted to a specific role",
)
async def get_role_permissions(
    role_id: uuid.UUID,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> List[PermissionRuleResponse]:
    """
    Retrieves all permission rules assigned directly to a role.
    """
    role = await db.get(Role, role_id)
    if not role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Role '{role_id}' not found.",
        )

    stmt = (
        select(PermissionRule)
        .where(PermissionRule.role_id == role_id)
        .order_by(PermissionRule.created_at.desc())
    )
    result = await db.execute(stmt)
    rules = result.scalars().all()
    return [PermissionRuleResponse.model_validate(r) for r in rules]


@router.delete(
    "/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a permission rule",
)
async def revoke_permission_rule(
    rule_id: uuid.UUID,
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """
    Revokes a permission rule by UUID.
    Atomically records an immutable AuditLog event in the current transaction.
    """
    rule = await db.get(PermissionRule, rule_id)
    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Permission rule '{rule_id}' not found.",
        )

    if rule.resource_type in (
        ResourceType.DATABASE,
        ResourceType.SCHEMA,
        ResourceType.TABLE,
        ResourceType.COLUMN,
    ):
        audit_entity_type = EntityType(rule.resource_type.value)
    else:
        audit_entity_type = EntityType.DATABASE

    audit_entity_id = (
        rule.resource_id if rule.resource_id is not None else rule.role_id
    )

    # Atomically record audit log
    await record_audit_event(
        session=db,
        actor_username=user_ctx.user.username,
        actor_id=user_ctx.user.id,
        action="REVOKE_PERMISSION",
        entity_type=audit_entity_type,
        entity_id=audit_entity_id,
        diff_jsonb={
            "revoked_rule_id": str(rule.id),
            "role_id": str(rule.role_id),
            "resource_type": rule.resource_type.value,
            "resource_id": str(rule.resource_id) if rule.resource_id else None,
            "action": rule.action.value,
            "effect": rule.effect.value,
        },
        flush=True,
    )

    await db.delete(rule)
    await db.flush()

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/check",
    response_model=EffectivePermissionCheckResponse,
    summary="Evaluate effective permission for an entity",
)
async def check_effective_permission(
    entity_type: EntityType = Query(..., description="Target entity type (DATABASE, SCHEMA, TABLE, COLUMN)"),
    entity_id: uuid.UUID = Query(..., description="Target entity UUID"),
    action: PermissionAction = Query(PermissionAction.VIEW_METADATA, description="Action to evaluate"),
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> EffectivePermissionCheckResponse:
    """
    Evaluates effective permissions for the authenticated user on a target resource,
    incorporating dual-dimension inheritance and DENY precedence.
    """
    effect = await CategoryService.resolve_effective_permission(
        session=db,
        user_ctx=user_ctx,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
    )

    return EffectivePermissionCheckResponse(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        effect=effect,
    )

