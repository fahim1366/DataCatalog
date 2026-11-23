from __future__ import annotations

import csv
import io
import logging
from typing import List, Optional
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import PlainTextResponse
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.api.dependencies.rbac import (
    UserContext,
    get_current_user,
    user_rule_matches,
)
from backend.src.core.database import get_db_session
from backend.src.models.catalog import (
    CatalogColumn,
    CatalogTable,
    DatabaseSchema,
    LifecycleStatus,
)
from backend.src.models.connections import DatabaseConnection
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

    # 1. Check explicit DENY for EXPORT_REPORTS
    deny_stmt = select(1).where(
        user_rule_matches(user_ctx),
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
        user_rule_matches(user_ctx),
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
    summary="Fast metadata completeness report with optional connection filtering",
)
async def get_completeness_report(
    connection_ids: Optional[List[uuid.UUID]] = Query(None, description="Filter by database connection IDs"),
    user_ctx: UserContext = Depends(require_reports_permission),
    db: AsyncSession = Depends(get_db_session),
) -> CompletenessReport:
    """
    Returns aggregated metadata completeness metrics across all registered databases or filtered ones.
    """
    return await report_service.get_metadata_completeness(db, connection_ids=connection_ids)


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
    connection_ids: Optional[List[uuid.UUID]] = Query(None, description="Filter by database connection IDs"),
    user_ctx: UserContext = Depends(require_reports_permission),
    db: AsyncSession = Depends(get_db_session),
) -> ClassificationReport:
    """
    Returns security classification and PII distribution metrics across all catalog columns or filtered ones.
    """
    return await report_service.get_classification_distribution(db, connection_ids=connection_ids)


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


@router.get(
    "/export",
    summary="Export enterprise data dictionary and executive reporting summary to XLSX, CSV, or JSON",
)
async def export_reports(
    format: str = Query("json", pattern="^(json|csv|xlsx)$"),
    connection_ids: Optional[List[uuid.UUID]] = Query(None, description="Filter by database connection IDs"),
    user_ctx: UserContext = Depends(require_reports_permission),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Exports executive reporting summary and enterprise data dictionary in XLSX, CSV (UTF-8 BOM), or JSON.
    Guarded by EXPORT_REPORTS permission.
    """
    completeness = await report_service.get_metadata_completeness(db, connection_ids=connection_ids)
    classification = await report_service.get_classification_distribution(db, connection_ids=connection_ids)

    # 1. Fetch Data Dictionary Tables
    table_stmt = (
        select(
            DatabaseConnection.database_name,
            DatabaseSchema.name.label("schema_name"),
            CatalogTable.name.label("table_name"),
            CatalogTable.table_type,
            CatalogTable.row_count_estimate,
            CatalogTable.size_bytes,
            CatalogTable.owner,
            CatalogTable.source_description,
            CatalogTable.approved_description,
            CatalogTable.description_source,
        )
        .join(DatabaseSchema, DatabaseSchema.id == CatalogTable.schema_id)
        .join(DatabaseConnection, DatabaseConnection.id == DatabaseSchema.connection_id)
        .where(CatalogTable.status != LifecycleStatus.DELETED)
    )
    if connection_ids:
        table_stmt = table_stmt.where(DatabaseConnection.id.in_(connection_ids))
    table_stmt = table_stmt.order_by(
        DatabaseConnection.database_name.asc(),
        DatabaseSchema.name.asc(),
        CatalogTable.name.asc(),
    )
    table_res = await db.execute(table_stmt)
    tables_data = table_res.all()

    # 2. Fetch Data Dictionary Columns
    col_stmt = (
        select(
            DatabaseConnection.database_name,
            DatabaseSchema.name.label("schema_name"),
            CatalogTable.name.label("table_name"),
            CatalogColumn.ordinal_position,
            CatalogColumn.name.label("column_name"),
            CatalogColumn.data_type,
            CatalogColumn.collation,
            CatalogColumn.is_primary_key,
            CatalogColumn.is_foreign_key,
            CatalogColumn.is_nullable,
            CatalogColumn.classification_level,
            CatalogColumn.source_description,
            CatalogColumn.approved_description,
            CatalogColumn.description_source,
        )
        .join(CatalogTable, CatalogTable.id == CatalogColumn.table_id)
        .join(DatabaseSchema, DatabaseSchema.id == CatalogTable.schema_id)
        .join(DatabaseConnection, DatabaseConnection.id == DatabaseSchema.connection_id)
        .where(
            CatalogColumn.status != LifecycleStatus.DELETED,
            CatalogTable.status != LifecycleStatus.DELETED,
        )
    )
    if connection_ids:
        col_stmt = col_stmt.where(DatabaseConnection.id.in_(connection_ids))
    col_stmt = col_stmt.order_by(
        DatabaseConnection.database_name.asc(),
        DatabaseSchema.name.asc(),
        CatalogTable.name.asc(),
        CatalogColumn.ordinal_position.asc(),
    )
    col_res = await db.execute(col_stmt)
    columns_data = col_res.all()

    # Format 1: XLSX (Multi-Sheet Styled RTL Excel Workbook)
    if format == "xlsx":
        wb = Workbook()

        header_font = Font(name="Tahoma", size=10, bold=True, color="FFFFFF")
        title_font = Font(name="Tahoma", size=13, bold=True, color="0F172A")
        section_font = Font(name="Tahoma", size=11, bold=True, color="1E293B")
        cell_font = Font(name="Tahoma", size=9)
        header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
        subheader_fill = PatternFill(start_color="334155", end_color="334155", fill_type="solid")
        zebra_fill = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")

        thin_border = Border(
            left=Side(style="thin", color="CBD5E1"),
            right=Side(style="thin", color="CBD5E1"),
            top=Side(style="thin", color="CBD5E1"),
            bottom=Side(style="thin", color="CBD5E1"),
        )
        align_center = Alignment(horizontal="center", vertical="center")
        align_right = Alignment(horizontal="right", vertical="center")

        # --- Sheet 1: خلاصه مدیریتی ---
        ws1 = wb.active
        ws1.title = "خلاصه مدیریتی"
        ws1.sheet_view.rightToLeft = True

        ws1.cell(row=1, column=1, value="گزارش خلاصه حاکمیت داده و کیفیت متادیتا سازمانی").font = title_font

        # Overall Metrics
        ws1.cell(row=3, column=1, value="شاخص کلیدی حاکمیت داده").font = header_font
        ws1.cell(row=3, column=1).fill = header_fill
        ws1.cell(row=3, column=1).alignment = align_center
        ws1.cell(row=3, column=2, value="مقدار").font = header_font
        ws1.cell(row=3, column=2).fill = header_fill
        ws1.cell(row=3, column=2).alignment = align_center

        kpis = [
            ("درصد کلی مستندسازی متادیتا", f"{completeness.overall_completeness_pct}%"),
            ("تعداد کل پایگاه‌های داده تحت پوشش", completeness.total_databases),
            ("تعداد کل جداول شناسایی شده", completeness.total_tables),
            ("تعداد کل ستون‌ها و فیلدها", completeness.total_columns),
            ("تعداد ستون‌های دارای طبقه‌بندی امنیتی", classification.total_classified_columns),
        ]
        curr_row = 4
        for label, val in kpis:
            c1 = ws1.cell(row=curr_row, column=1, value=label)
            c2 = ws1.cell(row=curr_row, column=2, value=val)
            for c in (c1, c2):
                c.font = cell_font
                c.border = thin_border
            c1.alignment = align_right
            c2.alignment = align_center
            curr_row += 1

        # Database Breakdown
        curr_row += 2
        ws1.cell(row=curr_row, column=1, value="تفکیک وضعیت مستندسازی به ازای پایگاه‌های داده").font = section_font
        curr_row += 1
        db_headers = ["نام پایگاه داده", "تعداد کل جداول", "جداول دارای شرح", "جداول دارای مالک", "درصد تکمیل متادیتا"]
        for col_idx, h in enumerate(db_headers, start=1):
            cell = ws1.cell(row=curr_row, column=col_idx, value=h)
            cell.font = header_font
            cell.fill = subheader_fill
            cell.alignment = align_center
            cell.border = thin_border

        curr_row += 1
        for db_item in completeness.database_breakdown:
            ws1.cell(row=curr_row, column=1, value=db_item.database_name).alignment = align_right
            ws1.cell(row=curr_row, column=2, value=db_item.table_count).alignment = align_center
            ws1.cell(row=curr_row, column=3, value=db_item.tables_with_desc).alignment = align_center
            ws1.cell(row=curr_row, column=4, value=db_item.tables_with_owner).alignment = align_center
            ws1.cell(row=curr_row, column=5, value=f"{db_item.completeness_pct}%").alignment = align_center
            for col_idx in range(1, 6):
                c = ws1.cell(row=curr_row, column=col_idx)
                c.font = cell_font
                c.border = thin_border
            curr_row += 1

        # Classification Breakdown
        curr_row += 2
        ws1.cell(row=curr_row, column=1, value="توزیع سطوح محرمانگی و داده‌های حساس (PII / Confidential)").font = section_font
        curr_row += 1
        class_headers = ["سطح محرمانگی", "تعداد ستون‌ها", "تعداد جداول درگیر"]
        for col_idx, h in enumerate(class_headers, start=1):
            cell = ws1.cell(row=curr_row, column=col_idx, value=h)
            cell.font = header_font
            cell.fill = subheader_fill
            cell.alignment = align_center
            cell.border = thin_border

        curr_row += 1
        for c_item in classification.breakdown:
            ws1.cell(row=curr_row, column=1, value=c_item.classification_level).alignment = align_center
            ws1.cell(row=curr_row, column=2, value=c_item.column_count).alignment = align_center
            ws1.cell(row=curr_row, column=3, value=c_item.affected_table_count).alignment = align_center
            for col_idx in range(1, 4):
                c = ws1.cell(row=curr_row, column=col_idx)
                c.font = cell_font
                c.border = thin_border
            curr_row += 1

        # --- Sheet 2: فرهنگ داده جداول ---
        ws2 = wb.create_sheet(title="فرهنگ داده جداول")
        ws2.sheet_view.rightToLeft = True

        tbl_headers = [
            "نام پایگاه داده",
            "نام اسکیما",
            "نام جدول",
            "نوع شیء",
            "تعداد رکورد (تخمینی)",
            "حجم (مگابایت)",
            "مالک سازمانی",
            "توضیحات مبدأ (Source)",
            "توضیحات مصوب حاکمیتی (Approved)",
            "منبع شرح (Description Source)",
        ]
        for col_idx, h in enumerate(tbl_headers, start=1):
            cell = ws2.cell(row=1, column=col_idx, value=h)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = align_center
            cell.border = thin_border

        for r_idx, t in enumerate(tables_data, start=2):
            ttype = t.table_type.value if hasattr(t.table_type, "value") else str(t.table_type)
            size_mb = round((t.size_bytes or 0) / 1024 / 1024, 2)
            ws2.cell(row=r_idx, column=1, value=t.database_name).alignment = align_right
            ws2.cell(row=r_idx, column=2, value=t.schema_name).alignment = align_right
            ws2.cell(row=r_idx, column=3, value=t.table_name).alignment = align_right
            ws2.cell(row=r_idx, column=4, value=ttype).alignment = align_center
            ws2.cell(row=r_idx, column=5, value=t.row_count_estimate or 0).alignment = align_center
            ws2.cell(row=r_idx, column=6, value=size_mb).alignment = align_center
            ws2.cell(row=r_idx, column=7, value=t.owner or "-").alignment = align_right
            ws2.cell(row=r_idx, column=8, value=t.source_description or "-").alignment = align_right
            ws2.cell(row=r_idx, column=9, value=t.approved_description or "-").alignment = align_right
            ws2.cell(row=r_idx, column=10, value=getattr(t, "description_source", "SOURCE_DB") or "SOURCE_DB").alignment = align_center
            for c_idx in range(1, 11):
                c = ws2.cell(row=r_idx, column=c_idx)
                c.font = cell_font
                c.border = thin_border
                if r_idx % 2 == 0:
                    c.fill = zebra_fill

        # --- Sheet 3: فرهنگ داده ستونها ---
        ws3 = wb.create_sheet(title="فرهنگ داده ستونها")
        ws3.sheet_view.rightToLeft = True

        col_headers = [
            "نام پایگاه داده",
            "نام اسکیما",
            "نام جدول",
            "ترتیب فیلد",
            "نام ستون",
            "نوع داده",
            "Collation",
            "نوع کلید",
            "پذیرش تهی (Nullable)",
            "سطح محرمانگی",
            "توضیحات مبدأ (Source)",
            "توضیحات مصوب حاکمیتی (Approved)",
            "منبع شرح (Description Source)",
        ]
        for col_idx, h in enumerate(col_headers, start=1):
            cell = ws3.cell(row=1, column=col_idx, value=h)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = align_center
            cell.border = thin_border

        for r_idx, c in enumerate(columns_data, start=2):
            clevel = c.classification_level.value if hasattr(c.classification_level, "value") else str(c.classification_level)
            key_type = "PK" if c.is_primary_key else ("FK" if c.is_foreign_key else "-")
            nullable_str = "بله" if c.is_nullable else "خیر"

            ws3.cell(row=r_idx, column=1, value=c.database_name).alignment = align_right
            ws3.cell(row=r_idx, column=2, value=c.schema_name).alignment = align_right
            ws3.cell(row=r_idx, column=3, value=c.table_name).alignment = align_right
            ws3.cell(row=r_idx, column=4, value=c.ordinal_position).alignment = align_center
            ws3.cell(row=r_idx, column=5, value=c.column_name).alignment = align_right
            ws3.cell(row=r_idx, column=6, value=c.data_type).alignment = align_center
            ws3.cell(row=r_idx, column=7, value=c.collation or "-").alignment = align_center
            ws3.cell(row=r_idx, column=8, value=key_type).alignment = align_center
            ws3.cell(row=r_idx, column=9, value=nullable_str).alignment = align_center
            ws3.cell(row=r_idx, column=10, value=clevel).alignment = align_center
            ws3.cell(row=r_idx, column=11, value=c.source_description or "-").alignment = align_right
            ws3.cell(row=r_idx, column=12, value=c.approved_description or "-").alignment = align_right
            ws3.cell(row=r_idx, column=13, value=getattr(c, "description_source", "SOURCE_DB") or "SOURCE_DB").alignment = align_center
            for c_idx in range(1, 14):
                cell_item = ws3.cell(row=r_idx, column=c_idx)
                cell_item.font = cell_font
                cell_item.border = thin_border
                if r_idx % 2 == 0:
                    cell_item.fill = zebra_fill

        # Auto-fit column widths across all sheets
        for sheet in (ws1, ws2, ws3):
            for col in sheet.columns:
                max_len = 0
                col_letter = get_column_letter(col[0].column)
                for cell in col:
                    val_str = str(cell.value or "")
                    if len(val_str) > max_len:
                        max_len = len(val_str)
                sheet.column_dimensions[col_letter].width = min(max(max_len + 4, 12), 50)

        out_stream = io.BytesIO()
        wb.save(out_stream)
        xlsx_bytes = out_stream.getvalue()

        return Response(
            content=xlsx_bytes,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=enterprise_data_dictionary.xlsx"},
        )

    # Format 2: CSV (UTF-8 with BOM for Excel compatibility)
    if format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow(["=== خلاصه مدیریتی حاکمیت داده ==="])
        writer.writerow(["شاخص حاکمیت داده", "مقدار"])
        writer.writerow(["درصد کلی تکمیل متادیتا", f"{completeness.overall_completeness_pct}%"])
        writer.writerow(["تعداد کل پایگاه‌های داده", completeness.total_databases])
        writer.writerow(["تعداد کل جداول", completeness.total_tables])
        writer.writerow(["تعداد کل ستون‌ها", completeness.total_columns])
        writer.writerow(["تعداد ستون‌های دارای طبقه‌بندی", classification.total_classified_columns])
        writer.writerow([])

        writer.writerow(["=== وضعیت مستندسازی پایگاه‌های داده ==="])
        writer.writerow(["نام پایگاه داده", "تعداد جداول", "جداول دارای شرح", "جداول دارای مالک", "درصد تکمیل"])
        for db_item in completeness.database_breakdown:
            writer.writerow([
                db_item.database_name,
                db_item.table_count,
                db_item.tables_with_desc,
                db_item.tables_with_owner,
                f"{db_item.completeness_pct}%",
            ])
        writer.writerow([])

        writer.writerow(["=== فرهنگ داده جداول ==="])
        writer.writerow([
            "نام پایگاه داده",
            "اسکیما",
            "نام جدول",
            "نوع شیء",
            "تعداد رکورد (تخمینی)",
            "حجم (مگابایت)",
            "مالک",
            "توضیحات مبدأ",
            "توضیحات مصوب حاکمیتی",
            "منبع شرح",
        ])
        for t in tables_data:
            ttype = t.table_type.value if hasattr(t.table_type, "value") else str(t.table_type)
            writer.writerow([
                t.database_name,
                t.schema_name,
                t.table_name,
                ttype,
                t.row_count_estimate or 0,
                round((t.size_bytes or 0) / 1024 / 1024, 2),
                t.owner or "",
                t.source_description or "",
                t.approved_description or "",
                getattr(t, "description_source", "SOURCE_DB") or "SOURCE_DB",
            ])
        writer.writerow([])

        writer.writerow(["=== فرهنگ داده ستون‌ها ==="])
        writer.writerow([
            "نام پایگاه داده",
            "اسکیما",
            "نام جدول",
            "شماره ردیف",
            "نام ستون",
            "نوع داده",
            "Collation",
            "نوع کلید",
            "پذیرش تهی (Nullable)",
            "سطح محرمانگی",
            "توضیحات مبدأ",
            "توضیحات مصوب حاکمیتی",
            "منبع شرح",
        ])
        for c in columns_data:
            clevel = c.classification_level.value if hasattr(c.classification_level, "value") else str(c.classification_level)
            writer.writerow([
                c.database_name,
                c.schema_name,
                c.table_name,
                c.ordinal_position,
                c.column_name,
                c.data_type,
                c.collation or "",
                "PK" if c.is_primary_key else ("FK" if c.is_foreign_key else "-"),
                "YES" if c.is_nullable else "NO",
                clevel,
                c.source_description or "",
                c.approved_description or "",
                getattr(c, "description_source", "SOURCE_DB") or "SOURCE_DB",
            ])

        csv_bytes = ("\ufeff" + output.getvalue()).encode("utf-8")
        return Response(
            content=csv_bytes,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=enterprise_data_dictionary.csv"},
        )

    # Format 3: JSON
    return {
        "completeness": completeness.model_dump(),
        "classification": classification.model_dump(),
        "tables": [
            {
                "database_name": t.database_name,
                "schema_name": t.schema_name,
                "table_name": t.table_name,
                "table_type": t.table_type.value if hasattr(t.table_type, "value") else str(t.table_type),
                "row_count_estimate": t.row_count_estimate,
                "size_bytes": t.size_bytes,
                "owner": t.owner,
                "source_description": t.source_description,
                "approved_description": t.approved_description,
                "description_source": getattr(t, "description_source", "SOURCE_DB") or "SOURCE_DB",
            }
            for t in tables_data
        ],
        "columns": [
            {
                "database_name": c.database_name,
                "schema_name": c.schema_name,
                "table_name": c.table_name,
                "ordinal_position": c.ordinal_position,
                "column_name": c.column_name,
                "data_type": c.data_type,
                "collation": c.collation,
                "is_primary_key": c.is_primary_key,
                "is_foreign_key": c.is_foreign_key,
                "is_nullable": c.is_nullable,
                "classification_level": c.classification_level.value if hasattr(c.classification_level, "value") else str(c.classification_level),
                "source_description": c.source_description,
                "approved_description": c.approved_description,
                "description_source": getattr(c, "description_source", "SOURCE_DB") or "SOURCE_DB",
            }
            for c in columns_data
        ],
    }


@router.get(
    "/preview",
    summary="Get executive reports data dictionary preview with source and approved descriptions",
)
async def get_report_preview(
    connection_ids: Optional[List[uuid.UUID]] = Query(None, description="Filter by database connection IDs"),
    limit: int = Query(100, ge=1, le=500),
    user_ctx: UserContext = Depends(require_reports_permission),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Returns data dictionary preview items with both source and approved descriptions, and description source.
    """
    stmt = (
        select(
            DatabaseConnection.database_name,
            DatabaseSchema.name.label("schema_name"),
            CatalogTable.name.label("table_name"),
            CatalogTable.table_type,
            CatalogTable.row_count_estimate,
            CatalogTable.size_bytes,
            CatalogTable.owner,
            CatalogTable.source_description,
            CatalogTable.approved_description,
            CatalogTable.description_source,
        )
        .join(DatabaseSchema, DatabaseSchema.id == CatalogTable.schema_id)
        .join(DatabaseConnection, DatabaseConnection.id == DatabaseSchema.connection_id)
        .where(CatalogTable.status != LifecycleStatus.DELETED)
    )
    if connection_ids:
        stmt = stmt.where(DatabaseConnection.id.in_(connection_ids))
    stmt = stmt.order_by(
        DatabaseConnection.database_name.asc(),
        DatabaseSchema.name.asc(),
        CatalogTable.name.asc(),
    ).limit(limit)

    res = await db.execute(stmt)
    rows = res.all()

    return [
        {
            "database_name": r.database_name,
            "schema_name": r.schema_name,
            "table_name": r.table_name,
            "table_type": r.table_type.value if hasattr(r.table_type, "value") else str(r.table_type),
            "row_count_estimate": r.row_count_estimate,
            "size_bytes": r.size_bytes,
            "owner": r.owner,
            "source_description": r.source_description,
            "approved_description": r.approved_description,
            "description_source": r.description_source,
        }
        for r in rows
    ]
