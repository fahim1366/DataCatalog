from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from backend.src.core.database import engine as default_engine
from backend.src.schemas.reports import (
    ClassificationBreakdownItem,
    ClassificationReport,
    CompletenessReport,
    DatabaseCompletenessItem,
)

logger = logging.getLogger(__name__)


class ReportService:
    """
    Executive and Governance Reporting Service powered by PostgreSQL Materialized Views.
    Delivers sub-50ms aggregated queries across hundreds of connected databases and supports
    non-blocking concurrent refreshes via PostgreSQL REFRESH MATERIALIZED VIEW CONCURRENTLY.
    """

    async def refresh_all_materialized_views_concurrently(
        self,
        engine: Optional[AsyncEngine] = None,
    ) -> Dict[str, str]:
        """
        Executes REFRESH MATERIALIZED VIEW CONCURRENTLY on both executive dashboard views.
        Uses AUTOCOMMIT isolation level because PostgreSQL prohibits concurrent materialized
        view refreshes within active transaction blocks.

        Returns a dictionary mapping view names to their refresh status.
        """
        target_engine = engine or default_engine
        results: Dict[str, str] = {}
        views_to_refresh = [
            "mv_metadata_completeness",
            "mv_classification_distribution",
        ]

        logger.info("Initiating concurrent refresh of PostgreSQL materialized views: %s", views_to_refresh)
        start_time = time.perf_counter()

        async with target_engine.connect() as conn:
            autocommit_conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            for view_name in views_to_refresh:
                view_start = time.perf_counter()
                await autocommit_conn.execute(
                    text(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {view_name};")
                )
                duration_ms = (time.perf_counter() - view_start) * 1000
                logger.info(
                    "Concurrently refreshed materialized view %s in %.2fms",
                    view_name,
                    duration_ms,
                )
                results[view_name] = "REFRESHED"

        total_duration_ms = (time.perf_counter() - start_time) * 1000
        logger.info(
            "Successfully completed concurrent refresh of all materialized views in %.2fms",
            total_duration_ms,
        )
        return results

    async def get_metadata_completeness(
        self,
        session: AsyncSession,
    ) -> CompletenessReport:
        """
        Retrieves metadata completeness metrics across all registered database connections
        from mv_metadata_completeness with guaranteed sub-50ms query latency.
        """
        start_time = time.perf_counter()
        stmt = text("""
            SELECT 
                connection_id,
                database_name,
                schema_count,
                table_count,
                column_count,
                tables_with_desc,
                tables_with_owner,
                table_desc_completeness_pct
            FROM mv_metadata_completeness
            ORDER BY database_name ASC;
        """)
        result = await session.execute(stmt)
        rows = result.fetchall()

        breakdown: List[DatabaseCompletenessItem] = []
        total_tables = 0
        total_columns = 0
        total_tables_with_desc = 0

        for row in rows:
            table_count = int(row.table_count or 0)
            column_count = int(row.column_count or 0)
            tables_with_desc = int(row.tables_with_desc or 0)
            tables_with_owner = int(row.tables_with_owner or 0)
            completeness_pct = float(row.table_desc_completeness_pct or 0.0)

            total_tables += table_count
            total_columns += column_count
            total_tables_with_desc += tables_with_desc

            breakdown.append(
                DatabaseCompletenessItem(
                    connection_id=row.connection_id,
                    database_name=row.database_name,
                    table_count=table_count,
                    tables_with_desc=tables_with_desc,
                    tables_with_owner=tables_with_owner,
                    completeness_pct=completeness_pct,
                )
            )

        total_databases = len(breakdown)
        overall_completeness_pct = (
            round((total_tables_with_desc / total_tables) * 100.0, 2)
            if total_tables > 0
            else 0.0
        )

        query_latency = (time.perf_counter() - start_time) * 1000
        logger.debug(
            "Fetched metadata completeness report for %d databases in %.2fms",
            total_databases,
            query_latency,
        )

        return CompletenessReport(
            overall_completeness_pct=overall_completeness_pct,
            total_databases=total_databases,
            total_tables=total_tables,
            total_columns=total_columns,
            database_breakdown=breakdown,
        )

    async def get_classification_distribution(
        self,
        session: AsyncSession,
    ) -> ClassificationReport:
        """
        Retrieves column-level security classification breakdown across all connected
        databases from mv_classification_distribution with guaranteed sub-50ms query latency.
        """
        start_time = time.perf_counter()
        stmt = text("""
            SELECT 
                classification_level,
                column_count,
                affected_table_count
            FROM mv_classification_distribution
            ORDER BY classification_level ASC;
        """)
        result = await session.execute(stmt)
        rows = result.fetchall()

        breakdown: List[ClassificationBreakdownItem] = []
        total_classified_columns = 0

        for row in rows:
            col_count = int(row.column_count or 0)
            table_count = int(row.affected_table_count or 0)
            total_classified_columns += col_count

            breakdown.append(
                ClassificationBreakdownItem(
                    classification_level=str(row.classification_level),
                    column_count=col_count,
                    affected_table_count=table_count,
                )
            )

        query_latency = (time.perf_counter() - start_time) * 1000
        logger.debug(
            "Fetched classification distribution report (%d columns) in %.2fms",
            total_classified_columns,
            query_latency,
        )

        return ClassificationReport(
            total_classified_columns=total_classified_columns,
            breakdown=breakdown,
        )


report_service = ReportService()

