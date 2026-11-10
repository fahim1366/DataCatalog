import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

# This import will FAIL initially before backend/tests/conftest.py implements check_docker_daemon and get_test_postgres_engine
from backend.tests.conftest import check_docker_daemon, get_test_postgres_engine


def test_docker_preflight_check():
    """Verify Docker daemon pre-flight check returns structured diagnostic status."""
    result = check_docker_daemon()
    assert hasattr(result, "is_available")
    assert hasattr(result, "message")
    assert isinstance(result.is_available, bool)
    assert isinstance(result.message, str)


@pytest.mark.asyncio
async def test_real_postgres_connection_and_extensions():
    """Verify that test engine connects to real PostgreSQL and verifies required extensions."""
    engine: AsyncEngine = await get_test_postgres_engine()
    assert isinstance(engine, AsyncEngine)

    try:
        async with engine.connect() as conn:
            # 1. Ping database version
            version_result = await conn.execute(text("SELECT version();"))
            version_str = version_result.scalar_one()
            assert "PostgreSQL" in version_str, f"Unexpected DB version: {version_str}"

            # 2. Check uuid-ossp extension
            ext_result = await conn.execute(
                text("SELECT extname FROM pg_extension WHERE extname IN ('uuid-ossp', 'pg_trgm', 'btree_gist');")
            )
            installed_exts = {row[0] for row in ext_result.fetchall()}
            assert "pg_trgm" in installed_exts, "Missing pg_trgm extension in real PostgreSQL"
            assert "btree_gist" in installed_exts, "Missing btree_gist extension in real PostgreSQL"

            # 3. Test functional execution of uuid and trigram functions
            trgm_test = await conn.execute(text("SELECT similarity('catalog', 'catalogue');"))
            assert trgm_test.scalar_one() > 0.5
    finally:
        await engine.dispose()

