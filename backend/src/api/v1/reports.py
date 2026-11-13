from __future__ import annotations

import logging
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.api.dependencies.rbac import UserContext, get_current_user
from backend.src.core.database import get_db_session
from backend.src.models.rbac import (
    PermissionAction,
    PermissionEffect,
    PermissionRule,
)
from backend.src.schemas.reports import (
    ClassificationReport,
    CompletenessReport,
    ReportRefreshResponse,
)
from backend.src.services.reporting.report_service import report_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["Executive Reporting"])


async def require_reports_permission(
    user_ctx: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> UserContext:
    """
    Enforces that the user has EXPORT_REPORTS permission or superadmin role.
    Explicit DENY overrides ALLOW.
    """
    if user_ctx.is_superadmin:
        return user_ctx

    if not user_ctx.role_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: User lacks reporting permission.",
        )

    # 1. Check explicit DENY for EXPORT_REPORTS
    deny_stmt = select(1).where(
        PermissionRule.role_id.in_(user_ctx.role_ids),
        PermissionRule.action == PermissionAction.EXPORT_REPORTS,
        PermissionRule.effect == PermissionEffect.DENY,
    )
    deny_result = await db.execute(deny_stmt)
    if deny_result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: User explicitly denied reporting access.",
        )

    # 2. Check explicit ALLOW for EXPORT_REPORTS
    allow_stmt = select(1).where(
        PermissionRule.role_id.in_(user_ctx.role_ids),
        PermissionRule.action == PermissionAction.EXPORT_REPORTS,
        PermissionRule.effect == PermissionEffect.ALLOW,
    )
    allow_result = await db.execute(allow_stmt)
    if allow_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: User lacks EXPORT_REPORTS permission.",
        )

    return user_ctx


@router.get(
    "/completeness",
    response_model=CompletenessReport,
    summary="Fast sub-200ms metadata completeness report powered by Materialized View",
)
async def get_completeness_report(
    user_ctx: UserContext = Depends(require_reports_permission),
    db: AsyncSession = Depends(get_db_session),
) -> CompletenessReport:
    """
    Returns aggregated metadata completeness metrics across all registered databases,
    queried directly from PostgreSQL mv_metadata_completeness in < 50ms.
    """
    return await report_service.get_metadata_completeness(db)


@router.get(
    "/classification-distribution",
    response_model=ClassificationReport,
    summary="Classification & PII breakdown metrics across all connected databases",
)
@router.get(
    "/classification",
    response_model=ClassificationReport,
    include_in_schema=False,
)
async def get_classification_distribution_report(
    user_ctx: UserContext = Depends(require_reports_permission),
    db: AsyncSession = Depends(get_db_session),
) -> ClassificationReport:
    """
    Returns security classification and PII distribution metrics across all catalog columns,
    queried directly from PostgreSQL mv_classification_distribution in < 50ms.
    """
    return await report_service.get_classification_distribution(db)


@router.post(
    "/refresh",
    response_model=ReportRefreshResponse,
    summary="Trigger non-blocking concurrent refresh of all reporting materialized views",
)
async def refresh_reporting_views(
    user_ctx: UserContext = Depends(require_reports_permission),
) -> ReportRefreshResponse:
    """
    Triggers REFRESH MATERIALIZED VIEW CONCURRENTLY for mv_metadata_completeness and
    mv_classification_distribution, ensuring zero read disruption for active dashboard users.
    """
    refreshed = await report_service.refresh_all_materialized_views_concurrently()
    return ReportRefreshResponse(
        status="SUCCESS",
        message="Materialized views refreshed concurrently.",
        refreshed_views=list(refreshed.keys()),
    )

