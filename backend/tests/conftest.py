import logging
import os
from dataclasses import dataclass
from typing import AsyncGenerator, Optional

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

logger = logging.getLogger(__name__)

# Fallback local real PostgreSQL connection for environments without Docker Desktop daemon running
LOCAL_PG_ASYNC_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:12345678@127.0.0.1:5432/enterprise_datacatalog",
)


@dataclass
class DockerPreflightResult:
    is_available: bool
    message: str
    image_ready: bool = False


def check_docker_daemon() -> DockerPreflightResult:
    """
    Performs pre-flight verification of Docker daemon availability and postgres:16 image presence.
    Returns structured diagnostics without crashing if Docker daemon is not active.
    """
    try:
        import docker

        client = docker.from_env(timeout=2)
        if not client.ping():
            return DockerPreflightResult(
                is_available=False,
                message="Docker daemon responded negatively to ping probe.",
            )

        # Check if postgres:16 image is available locally
        images = [tag for img in client.images.list() if img.tags for tag in img.tags]
        has_pg16 = any("postgres:16" in tag for tag in images)

        return DockerPreflightResult(
            is_available=True,
            message="Docker daemon is active and responsive.",
            image_ready=has_pg16,
        )
    except Exception as exc:
        return DockerPreflightResult(
            is_available=False,
            message=f"Docker daemon is not available: {str(exc)}",
        )


async def get_test_postgres_engine() -> AsyncEngine:
    """
    Initializes a real PostgreSQL 16+ async SQLAlchemy engine.
    If Docker is responsive, utilizes testcontainers-postgres.
    If Docker daemon is offline, binds to the live dedicated real PostgreSQL instance.
    Guarantees that uuid-ossp, pg_trgm, and btree_gist extensions are active.
    """
    preflight = check_docker_daemon()
    container_instance = None
    target_url = LOCAL_PG_ASYNC_URL

    if preflight.is_available:
        try:
            from testcontainers.postgres import PostgresContainer

            logger.info("Docker daemon is active. Initializing testcontainers PostgresContainer('postgres:16')...")
            container = PostgresContainer("postgres:16")
            container.start()
            container_instance = container

            # Convert sync URL to asyncpg driver URL
            sync_url = container.get_connection_url()
            target_url = sync_url.replace("postgresql://", "postgresql+asyncpg://").replace(
                "postgresql+psycopg2://", "postgresql+asyncpg://"
            )
        except Exception as exc:
            logger.warning(
                f"Testcontainers start failed ({exc}). Falling back to dedicated real PostgreSQL instance: {LOCAL_PG_ASYNC_URL}"
            )
            target_url = LOCAL_PG_ASYNC_URL
    else:
        logger.info(
            f"Docker pre-flight check: {preflight.message}. Connecting to verified real PostgreSQL instance at {LOCAL_PG_ASYNC_URL}"
        )

    engine = create_async_engine(
        target_url,
        echo=False,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )

    # Pre-load required enterprise extensions in real PostgreSQL
    async with engine.begin() as conn:
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";'))
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pg_trgm";'))
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "btree_gist";'))

    return engine


@pytest_asyncio.fixture(scope="function")
async def test_db_engine() -> AsyncGenerator[AsyncEngine, None]:
    """Pytest fixture providing an isolated real PostgreSQL async engine."""
    engine = await get_test_postgres_engine()
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def test_db_session(test_db_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """Pytest fixture providing a real transactional database session with per-test rollback."""
    session_factory = async_sessionmaker(
        test_db_engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )
    async with session_factory() as session:
        yield session
        await session.rollback()

