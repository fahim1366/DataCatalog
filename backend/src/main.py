from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from backend.src.api.v1 import api_v1_router
from backend.src.core.config import settings
from backend.src.core.database import check_database_health, dispose_engine, engine

logger = logging.getLogger(__name__)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Enforces standard HTTP security hardening headers across all REST API responses."""

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan context manager for startup pre-flight and graceful shutdown."""
    # Ensure idempotent schema safety for sync_jobs table (e.g. priority column)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("ALTER TABLE sync_jobs ADD COLUMN IF NOT EXISTS priority INTEGER NOT NULL DEFAULT 0;")
            )
            logger.info("Idempotent schema check passed: priority column ensured on sync_jobs.")
    except Exception as exc:
        logger.warning("Could not execute startup schema check on sync_jobs: %s", exc)
    yield
    await dispose_engine()


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Enterprise Data Catalog REST API",
    lifespan=lifespan,
)

# CORS middleware for enterprise Angular frontend (strictly allowing port 4200 & localhost dev server)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:4200",
        "http://127.0.0.1:4200",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security headers middleware
app.add_middleware(SecurityHeadersMiddleware)

# Wire API routes
app.include_router(api_v1_router, prefix=settings.API_V1_STR)


@app.get("/health", tags=["Health"])
async def health_check():
    """Health check endpoint checking database connectivity."""
    db_health = await check_database_health()
    return {
        "status": "UP",
        "database": db_health,
    }
