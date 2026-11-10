from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
import time
from typing import Any, Dict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.src.core.config import settings

# Enterprise-grade async engine pool configuration for high concurrency
engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DB_ECHO,
    future=True,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_recycle=settings.DB_POOL_RECYCLE,
    pool_pre_ping=settings.DB_POOL_PRE_PING,
)

async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency yielding a transactional AsyncSession.
    Automatically commits changes when the request handler finishes cleanly.
    Automatically rolls back transaction if an unhandled exception is raised.
    Always closes the session in finally to release connection back to pool.
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def get_session_context() -> AsyncGenerator[AsyncSession, None]:
    """
    Async context manager for background tasks, CLI commands, and worker jobs.
    Usage:
        async with get_session_context() as session:
            await session.execute(...)
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def check_database_health() -> Dict[str, Any]:
    """
    Lightweight health check probe measuring connection latency and database version.
    Returns structured diagnostic dictionary.
    """
    start_time = time.perf_counter()
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT version();"))
        version_str: str = str(result.scalar_one())
    elapsed_ms = (time.perf_counter() - start_time) * 1000.0

    return {
        "status": "HEALTHY",
        "database_version": version_str,
        "latency_ms": round(elapsed_ms, 2),
        "pool_size": engine.pool.size(),
        "checked_out_connections": engine.pool.checkedout(),
        "overflow_connections": engine.pool.overflow(),
    }


async def dispose_engine() -> None:
    """Disposes all connection pool sockets cleanly during application shutdown."""
    await engine.dispose()
