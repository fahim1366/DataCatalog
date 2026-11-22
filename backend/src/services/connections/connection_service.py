from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.src.models.connections import DatabaseConnection, DatabaseEngine, SyncSchedule
from backend.src.models.rbac import Category, CategoryAssignment, EntityType
from backend.src.schemas.connections import (
    ConnectionCreateRequest,
    ConnectionResponse,
    ConnectionUpdateRequest,
    SyncScheduleResponse,
)
from backend.src.services.audit.audit_service import record_audit_event
from backend.src.services.connections.cron_validator import validate_cron_expression
from backend.src.services.connections.ping_service import PingResult, ping_service
from backend.src.services.crypto.envelope import EnvelopeEncryptionService

logger = logging.getLogger(__name__)


def mask_credentials(credentials: Dict[str, Any]) -> Dict[str, str]:
    """Replaces secret values with asterisks to prevent credential leakage."""
    masked: Dict[str, str] = {}
    for key, value in credentials.items():
        lower_k = key.lower()
        if any(secret_term in lower_k for secret_term in ["pass", "secret", "key", "token", "cert", "credential"]):
            masked[key] = "********"
        else:
            masked[key] = str(value)
    return masked


class ConnectionService:
    """
    Enterprise Database Connection Registry Service.

    - Protects database secrets via AES-256-GCM Envelope Encryption (unique DEK per connection).
    - Enforces 5-part cron syntax validation on sync schedules.
    - Atomically emits immutable AuditLog records in PostgreSQL.
    - Decrypts credentials strictly transiently in memory during connection test pings.
    """

    def __init__(self, crypto_service: Optional[EnvelopeEncryptionService] = None) -> None:
        self.crypto = crypto_service or EnvelopeEncryptionService()

    def serialize_response(
        self,
        connection: DatabaseConnection,
        category_id: Optional[uuid.UUID] = None,
        category_name: Optional[str] = None,
    ) -> ConnectionResponse:
        """Converts DatabaseConnection ORM entity into ConnectionResponse with masked credentials."""
        # Decrypt transiently in memory to extract keys and mask secret values
        try:
            creds = self.crypto.decrypt_credentials(
                bytes(connection.encrypted_dek), bytes(connection.encrypted_credentials)
            )
            masked = mask_credentials(creds)
        except Exception:
            masked = {"credentials": "••••••••"}

        sync_resp = None
        if "sync_schedule" in connection.__dict__ and connection.sync_schedule is not None:
            sync_resp = SyncScheduleResponse.model_validate(connection.sync_schedule)

        eff_cat_id = category_id if category_id is not None else getattr(connection, "_category_id", None)
        eff_cat_name = category_name if category_name is not None else getattr(connection, "_category_name", None)

        return ConnectionResponse(
            id=connection.id,
            name=connection.name,
            engine_type=connection.engine_type,
            host=connection.host,
            port=connection.port,
            database_name=connection.database_name,
            default_schema=connection.default_schema,
            owner=connection.owner,
            is_active=connection.is_active,
            category_id=eff_cat_id,
            category_name=eff_cat_name,
            custom_params=connection.custom_params or {},
            masked_credentials=masked,
            sync_schedule=sync_resp,
            created_at=connection.created_at,
            updated_at=connection.updated_at,
        )

    async def create_connection(
        self,
        session: AsyncSession,
        request: ConnectionCreateRequest,
        actor_username: str,
        actor_id: Optional[uuid.UUID] = None,
    ) -> DatabaseConnection:
        """
        Registers a new database connection with envelope-encrypted credentials and audit logging.
        """
        # 1. Validate cron expression if provided
        if request.cron_expression:
            validate_cron_expression(request.cron_expression)

        # 2. Encrypt credentials with unique ephemeral DEK
        enc_dek, enc_creds = self.crypto.encrypt_credentials(request.credentials)

        connection_id = uuid.uuid4()
        connection = DatabaseConnection(
            id=connection_id,
            name=request.name,
            engine_type=request.engine_type,
            host=request.host,
            port=request.port,
            database_name=request.database_name,
            default_schema=request.default_schema,
            owner=request.owner or actor_username,
            kek_version=1,
            encrypted_dek=enc_dek,
            encrypted_credentials=enc_creds,
            custom_params=request.custom_params or {},
            is_active=True,
        )
        session.add(connection)

        # 3. Create SyncSchedule if requested
        if request.cron_expression:
            schedule = SyncSchedule(
                id=uuid.uuid4(),
                connection_id=connection_id,
                cron_expression=request.cron_expression.strip(),
                is_enabled=True,
            )
            session.add(schedule)
            connection.sync_schedule = schedule
        else:
            connection.sync_schedule = None

        # 4. Handle Category Assignment (enforce leaf category)
        cat_name: Optional[str] = None
        if request.category_id:
            cat = await session.get(Category, request.category_id)
            if not cat:
                raise ValueError("دسته‌بندی / موضوع انتخاب‌شده یافت نشد.")
            child_count_stmt = select(func.count(Category.id)).where(Category.parent_id == request.category_id)
            child_count = (await session.execute(child_count_stmt)).scalar() or 0
            if child_count > 0:
                raise ValueError("فقط برگ‌های درخت موضوعات (دسته‌بندی‌های بدون زیرمجموعه) قابل انتساب به پایگاه‌داده هستند.")
            assignment = CategoryAssignment(
                id=uuid.uuid4(),
                category_id=request.category_id,
                entity_type=EntityType.DATABASE,
                entity_id=connection_id,
            )
            session.add(assignment)
            cat_name = cat.name

        connection._category_id = request.category_id
        connection._category_name = cat_name

        await session.flush()

        # 5. Record immutable audit log entry in the same transaction
        await record_audit_event(
            session=session,
            actor_username=actor_username,
            actor_id=actor_id,
            action="CREATE_CONNECTION",
            entity_type=EntityType.DATABASE,
            entity_id=connection_id,
            diff_jsonb={
                "name": connection.name,
                "engine_type": connection.engine_type.value,
                "host": connection.host,
                "port": connection.port,
                "database_name": connection.database_name,
                "has_schedule": request.cron_expression is not None,
                "category_id": str(request.category_id) if request.category_id else None,
            },
            flush=True,
        )

        return connection

    async def get_connection(
        self, session: AsyncSession, connection_id: uuid.UUID
    ) -> Optional[DatabaseConnection]:
        """Retrieves connection by ID including sync_schedule and category assignment."""
        stmt = (
            select(DatabaseConnection)
            .where(DatabaseConnection.id == connection_id)
            .options(selectinload(DatabaseConnection.sync_schedule))
        )
        result = await session.execute(stmt)
        conn = result.scalar_one_or_none()
        if conn is not None:
            assign_stmt = (
                select(CategoryAssignment.category_id, Category.name)
                .join(Category, Category.id == CategoryAssignment.category_id)
                .where(
                    CategoryAssignment.entity_id == connection_id,
                    CategoryAssignment.entity_type == EntityType.DATABASE,
                )
            )
            assign_res = await session.execute(assign_stmt)
            row = assign_res.first()
            if row is not None:
                conn._category_id = row[0]
                conn._category_name = row[1]
            else:
                conn._category_id = None
                conn._category_name = None
        return conn

    async def list_connections(
        self,
        session: AsyncSession,
        is_active: Optional[bool] = None,
        search: Optional[str] = None,
        engine_type: Optional[Any] = None,
        host: Optional[str] = None,
        owner: Optional[str] = None,
    ) -> List[DatabaseConnection]:
        """Lists registered database connections with their assigned leaf category and optional search/filter criteria."""
        stmt = select(DatabaseConnection).options(
            selectinload(DatabaseConnection.sync_schedule)
        )
        if is_active is not None:
            stmt = stmt.where(DatabaseConnection.is_active == is_active)
        if engine_type is not None:
            stmt = stmt.where(DatabaseConnection.engine_type == engine_type)
        if host is not None and host.strip():
            stmt = stmt.where(DatabaseConnection.host.ilike(f"%{host.strip()}%"))
        if owner is not None and owner.strip():
            stmt = stmt.where(DatabaseConnection.owner.ilike(f"%{owner.strip()}%"))
        if search is not None and search.strip():
            term = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(
                    DatabaseConnection.name.ilike(term),
                    DatabaseConnection.database_name.ilike(term),
                    DatabaseConnection.host.ilike(term),
                    DatabaseConnection.owner.ilike(term),
                )
            )
        stmt = stmt.order_by(DatabaseConnection.name.asc())
        result = await session.execute(stmt)
        connections = list(result.scalars().all())

        # Load category assignments for all connections
        assign_stmt = (
            select(
                CategoryAssignment.entity_id,
                CategoryAssignment.category_id,
                Category.name,
            )
            .join(Category, Category.id == CategoryAssignment.category_id)
            .where(CategoryAssignment.entity_type == EntityType.DATABASE)
        )
        assign_res = await session.execute(assign_stmt)
        cat_map = {row[0]: (row[1], row[2]) for row in assign_res.all()}

        for c in connections:
            if c.id in cat_map:
                c._category_id, c._category_name = cat_map[c.id]
            else:
                c._category_id, c._category_name = None, None

        return connections

    async def test_connection(
        self, session: AsyncSession, connection_id: uuid.UUID
    ) -> Optional[PingResult]:
        """
        Executes a transient dialect ping test for a registered connection.
        Decrypts credentials in memory solely for the duration of the probe.
        """
        connection = await self.get_connection(session, connection_id)
        if connection is None:
            return None

        # Decrypt credentials transiently
        decrypted_creds = self.crypto.decrypt_credentials(
            bytes(connection.encrypted_dek), bytes(connection.encrypted_credentials)
        )

        # Probe dialect connectivity
        return await ping_service.ping(
            engine_type=connection.engine_type,
            host=connection.host,
            port=connection.port,
            database_name=connection.database_name,
            credentials=decrypted_creds,
            custom_params=connection.custom_params or {},
        )


connection_service = ConnectionService()
