from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.src.models.connections import DatabaseConnection, DatabaseEngine, SyncSchedule
from backend.src.models.rbac import EntityType
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

    def serialize_response(self, connection: DatabaseConnection) -> ConnectionResponse:
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

        await session.flush()

        # 4. Record immutable audit log entry in the same transaction
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
            },
            flush=True,
        )

        return connection

    async def get_connection(
        self, session: AsyncSession, connection_id: uuid.UUID
    ) -> Optional[DatabaseConnection]:
        """Retrieves connection by ID including sync_schedule."""
        stmt = (
            select(DatabaseConnection)
            .where(DatabaseConnection.id == connection_id)
            .options(selectinload(DatabaseConnection.sync_schedule))
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_connections(
        self, session: AsyncSession, is_active: Optional[bool] = None
    ) -> List[DatabaseConnection]:
        """Lists registered database connections."""
        stmt = select(DatabaseConnection).options(
            selectinload(DatabaseConnection.sync_schedule)
        )
        if is_active is not None:
            stmt = stmt.where(DatabaseConnection.is_active == is_active)
        stmt = stmt.order_by(DatabaseConnection.name.asc())
        result = await session.execute(stmt)
        return list(result.scalars().all())

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
