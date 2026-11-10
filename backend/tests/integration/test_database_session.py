import asyncio
import uuid
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Imports that require the full production database session module
from backend.src.core.database import (
    engine,
    async_session_factory,
    get_db_session,
    get_session_context,
    check_database_health,
    dispose_engine,
)
from backend.src.core.config import settings
from backend.src.models.identity import Role


@pytest.mark.asyncio
async def test_database_engine_configuration():
    """Verify that the engine pool is configured to target enterprise scale."""
    assert engine.pool.size() == settings.DB_POOL_SIZE
    assert engine.pool._max_overflow == settings.DB_MAX_OVERFLOW
    assert engine.pool._recycle == settings.DB_POOL_RECYCLE
    assert engine.pool._pre_ping is True


@pytest.mark.asyncio
async def test_database_health_check():
    """Verify health check function returns latency, version, and healthy status."""
    health = await check_database_health()
    assert health["status"] == "HEALTHY"
    assert "latency_ms" in health
    assert isinstance(health["latency_ms"], float)
    assert health["latency_ms"] >= 0.0
    assert "PostgreSQL" in health["database_version"]


@pytest.mark.asyncio
async def test_get_db_session_commit_on_success():
    """Verify that get_db_session commits state mutations upon normal block exit."""
    role_name = f"SessionTestRole_{uuid.uuid4().hex[:8]}"

    # Use dependency generator
    gen = get_db_session()
    session = await anext(gen)
    try:
        new_role = Role(name=role_name, description="Committed via get_db_session")
        session.add(new_role)
        await session.flush()
    finally:
        # Normal generator completion triggers commit in finally
        try:
            await anext(gen)
        except StopAsyncIteration:
            pass

    # Verify committed in fresh session
    async with async_session_factory() as verify_session:
        result = await verify_session.execute(
            text("SELECT name FROM roles WHERE name = :name"),
            {"name": role_name},
        )
        assert result.scalar_one_or_none() == role_name

        # Clean up
        await verify_session.execute(
            text("DELETE FROM roles WHERE name = :name"),
            {"name": role_name},
        )
        await verify_session.commit()


@pytest.mark.asyncio
async def test_get_db_session_rollback_on_exception():
    """Verify that get_db_session automatically rolls back on unhandled exception."""
    role_name = f"RollbackRole_{uuid.uuid4().hex[:8]}"

    gen = get_db_session()
    session = await anext(gen)
    try:
        new_role = Role(name=role_name, description="Should be rolled back")
        session.add(new_role)
        await session.flush()
        # Simulate unhandled exception inside endpoint
        raise RuntimeError("Simulated API failure")
    except RuntimeError:
        try:
            await gen.athrow(RuntimeError("Simulated API failure"))
        except RuntimeError:
            pass

    # Verify NOT committed
    async with async_session_factory() as verify_session:
        result = await verify_session.execute(
            text("SELECT name FROM roles WHERE name = :name"),
            {"name": role_name},
        )
        assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_get_session_context_context_manager():
    """Verify get_session_context works as an async context manager for non-FastAPI tasks."""
    role_name = f"ContextRole_{uuid.uuid4().hex[:8]}"

    async with get_session_context() as session:
        new_role = Role(name=role_name, description="Context manager test")
        session.add(new_role)

    # Verify committed
    async with async_session_factory() as verify_session:
        result = await verify_session.execute(
            text("SELECT name FROM roles WHERE name = :name"),
            {"name": role_name},
        )
        assert result.scalar_one_or_none() == role_name

        # Clean up
        await verify_session.execute(
            text("DELETE FROM roles WHERE name = :name"),
            {"name": role_name},
        )
        await verify_session.commit()


@pytest.mark.asyncio
async def test_concurrent_connection_checkout():
    """Verify pool handles concurrent session operations without deadlock or exhaustion."""
    async def run_query(idx: int) -> int:
        async with async_session_factory() as session:
            result = await session.execute(text("SELECT CAST(:idx AS INT) AS val"), {"idx": idx})
            return result.scalar_one()

    # Launch 25 concurrent queries (exceeding base pool size 20, testing max_overflow)
    tasks = [run_query(i) for i in range(25)]
    results = await asyncio.gather(*tasks)
    assert results == list(range(25))
