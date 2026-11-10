from __future__ import annotations

import base64
import json
from typing import Any, Dict, List, Optional, Tuple
import uuid
from sqlalchemy import (
    Select,
    and_,
    func,
    or_,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.src.api.dependencies.rbac import UserContext, build_table_rbac_filter
from backend.src.models.catalog import (
    CatalogColumn,
    CatalogTable,
    DatabaseSchema,
    LifecycleStatus,
    Tag,
    TagAssignment,
)
from backend.src.models.connections import DatabaseConnection
from backend.src.models.rbac import EntityType, PermissionAction


def encode_cursor(sort_val: str, row_id: uuid.UUID) -> str:
    """Encodes a sort value and UUID into an opaque URL-safe Base64 cursor."""
    payload = {"val": sort_val, "id": str(row_id)}
    return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8")


def decode_cursor(cursor_str: str) -> Tuple[str, uuid.UUID]:
    """Decodes a Base64 keyset cursor into sort value and row UUID."""
    raw = base64.b64decode(cursor_str.encode("utf-8")).decode("utf-8")
    data = json.loads(raw)
    return str(data["val"]), uuid.UUID(str(data["id"]))


class SearchService:
    """
    High-performance Search and Keyset Navigation Service.
    Enforces PostgreSQL GIN to_tsquery and pg_trgm similarity with SQL-pushed RBAC.
    """

    async def search(
        self,
        session: AsyncSession,
        user_ctx: UserContext,
        query: str,
        limit: int = 50,
        cursor: Optional[str] = None,
        include_deleted: bool = False,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Executes keyset-paginated search across catalog tables filtered directly in SQL by RBAC.
        Uses PostgreSQL tsvector GIN index and pg_trgm fallback.
        """
        clean_query = query.strip()
        if not clean_query:
            return [], {"has_next_page": False, "next_cursor": None, "limit": limit}

        # PostgreSQL full-text query and trigram similarity expressions
        ts_query_expr = func.plainto_tsquery("english", clean_query)
        ts_match = CatalogTable.search_vector.op("@@")(ts_query_expr)
        trgm_match = CatalogTable.name.ilike(f"%{clean_query}%")
        rank_expr = func.coalesce(
            func.ts_rank(CatalogTable.search_vector, ts_query_expr), 0.0
        ) + func.similarity(CatalogTable.name, clean_query)

        stmt: Select = (
            select(
                CatalogTable,
                DatabaseSchema.name.label("schema_name"),
                DatabaseConnection.database_name.label("database_name"),
                rank_expr.label("rank_score"),
            )
            .join(DatabaseSchema, DatabaseSchema.id == CatalogTable.schema_id)
            .join(DatabaseConnection, DatabaseConnection.id == DatabaseSchema.connection_id)
            .where(or_(ts_match, trgm_match))
        )

        # Soft-delete filter
        if not include_deleted:
            stmt = stmt.where(CatalogTable.status != LifecycleStatus.DELETED)

        # Apply SQL-pushed RBAC filter
        rbac_filter = build_table_rbac_filter(user_ctx=user_ctx, action=PermissionAction.VIEW_METADATA)
        stmt = rbac_filter(stmt)

        # Keyset pagination condition: (name, id) > (cursor_val, cursor_id)
        if cursor:
            try:
                cursor_val, cursor_id = decode_cursor(cursor)
                stmt = stmt.where(
                    or_(
                        CatalogTable.name > cursor_val,
                        and_(CatalogTable.name == cursor_val, CatalogTable.id > cursor_id),
                    )
                )
            except Exception:
                # Invalid cursor format fallback -> ignore cursor
                pass

        # Deterministic sorting for keyset cursor pagination
        stmt = stmt.order_by(CatalogTable.name.asc(), CatalogTable.id.asc()).limit(limit + 1)

        result = await session.execute(stmt)
        rows = result.all()

        has_next_page = len(rows) > limit
        selected_rows = rows[:limit] if has_next_page else rows

        items: List[Dict[str, Any]] = []
        for row in selected_rows:
            tbl: CatalogTable = row[0]
            schema_name: str = row[1]
            db_name: str = row[2]
            rank_val: float = float(row[3]) if row[3] is not None else 1.0

            items.append(
                {
                    "id": tbl.id,
                    "entity_type": "TABLE",
                    "name": tbl.name,
                    "database_name": db_name,
                    "schema_name": schema_name,
                    "table_name": None,
                    "effective_description": tbl.effective_description,
                    "rank": round(rank_val, 4),
                    "classification_level": None,
                    "status": tbl.status.value,
                }
            )

        next_cursor = None
        if has_next_page and selected_rows:
            last_table: CatalogTable = selected_rows[-1][0]
            next_cursor = encode_cursor(last_table.name, last_table.id)

        page_info = {
            "has_next_page": has_next_page,
            "next_cursor": next_cursor,
            "limit": limit,
        }

        return items, page_info

    async def list_tables_by_schema(
        self,
        session: AsyncSession,
        schema_id: uuid.UUID,
        user_ctx: UserContext,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Keyset-paginated listing of tables for a specific schema, filtered by RBAC.
        """
        stmt: Select = (
            select(CatalogTable)
            .where(
                CatalogTable.schema_id == schema_id,
                CatalogTable.status != LifecycleStatus.DELETED,
            )
        )

        # Apply RBAC filter
        rbac_filter = build_table_rbac_filter(user_ctx=user_ctx, action=PermissionAction.VIEW_METADATA)
        stmt = rbac_filter(stmt)

        # Apply keyset cursor
        if cursor:
            try:
                cursor_val, cursor_id = decode_cursor(cursor)
                stmt = stmt.where(
                    or_(
                        CatalogTable.name > cursor_val,
                        and_(CatalogTable.name == cursor_val, CatalogTable.id > cursor_id),
                    )
                )
            except Exception:
                pass

        stmt = stmt.order_by(CatalogTable.name.asc(), CatalogTable.id.asc()).limit(limit + 1)
        result = await session.execute(stmt)
        tables = list(result.scalars().all())

        has_next_page = len(tables) > limit
        selected_tables = tables[:limit] if has_next_page else tables

        items = [
            {
                "id": t.id,
                "schema_id": t.schema_id,
                "name": t.name,
                "table_type": t.table_type.value,
                "row_count_estimate": t.row_count_estimate,
                "size_bytes": t.size_bytes,
                "effective_description": t.effective_description,
                "owner": t.owner,
                "status": t.status.value,
            }
            for t in selected_tables
        ]

        next_cursor = None
        if has_next_page and selected_tables:
            last = selected_tables[-1]
            next_cursor = encode_cursor(last.name, last.id)

        page_info = {
            "has_next_page": has_next_page,
            "next_cursor": next_cursor,
            "limit": limit,
        }

        return items, page_info

    async def get_table_details(
        self,
        session: AsyncSession,
        table_id: uuid.UUID,
        user_ctx: UserContext,
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieves table details including columns and active tags, verified against RBAC.
        Returns None if table does not exist or user lacks VIEW_METADATA permission.
        """
        stmt = (
            select(
                CatalogTable,
                DatabaseSchema.name.label("schema_name"),
                DatabaseConnection.database_name.label("database_name"),
            )
            .join(DatabaseSchema, DatabaseSchema.id == CatalogTable.schema_id)
            .join(DatabaseConnection, DatabaseConnection.id == DatabaseSchema.connection_id)
            .where(
                CatalogTable.id == table_id,
                CatalogTable.status != LifecycleStatus.DELETED,
            )
            .options(
                selectinload(CatalogTable.columns),
            )
        )

        rbac_filter = build_table_rbac_filter(user_ctx=user_ctx, action=PermissionAction.VIEW_METADATA)
        stmt = rbac_filter(stmt)

        result = await session.execute(stmt)
        row = result.first()
        if not row:
            return None

        tbl: CatalogTable = row[0]
        schema_name: str = row[1]
        database_name: str = row[2]

        # Fetch tags assigned to this table
        tag_stmt = (
            select(Tag)
            .join(TagAssignment, TagAssignment.tag_id == Tag.id)
            .where(
                TagAssignment.entity_type == EntityType.TABLE,
                TagAssignment.entity_id == table_id,
            )
        )
        tag_result = await session.execute(tag_stmt)
        tags = [
            {"id": tag.id, "name": tag.name, "color": tag.color}
            for tag in tag_result.scalars().all()
        ]

        columns_data = [
            {
                "id": col.id,
                "table_id": col.table_id,
                "name": col.name,
                "ordinal_position": col.ordinal_position,
                "data_type": col.data_type,
                "raw_data_type": col.raw_data_type,
                "is_nullable": col.is_nullable,
                "is_primary_key": col.is_primary_key,
                "is_foreign_key": col.is_foreign_key,
                "default_value": col.default_value,
                "source_description": col.source_description,
                "approved_description": col.approved_description,
                "effective_description": col.effective_description,
                "classification_level": col.classification_level.value,
                "status": col.status.value,
            }
            for col in tbl.columns
            if col.status != LifecycleStatus.DELETED
        ]

        return {
            "id": tbl.id,
            "schema_id": tbl.schema_id,
            "schema_name": schema_name,
            "database_name": database_name,
            "name": tbl.name,
            "table_type": tbl.table_type.value,
            "row_count_estimate": tbl.row_count_estimate,
            "size_bytes": tbl.size_bytes,
            "source_description": tbl.source_description,
            "approved_description": tbl.approved_description,
            "effective_description": tbl.effective_description,
            "owner": tbl.owner,
            "status": tbl.status.value,
            "columns": columns_data,
            "tags": tags,
        }


search_service = SearchService()

