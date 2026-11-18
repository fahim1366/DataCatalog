import pytest
from httpx import ASGITransport, AsyncClient
from backend.src.main import app


@pytest.mark.asyncio
async def test_security_headers_middleware_on_health_check():
    """Assert X-Content-Type-Options: nosniff and X-Frame-Options: DENY are present on all responses."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "DENY"


@pytest.mark.asyncio
async def test_security_headers_middleware_on_unauthorized_endpoints():
    """Security headers must also be present on 401/403/404 responses."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/reports/completeness")
        assert resp.status_code == 401
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "DENY"
