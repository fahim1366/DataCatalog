from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.src.api.v1 import api_v1_router
from backend.src.core.config import settings
from backend.src.core.database import check_database_health, dispose_engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan context manager for startup pre-flight and graceful shutdown."""
    yield
    await dispose_engine()


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Enterprise Data Catalog REST API",
    lifespan=lifespan,
)

# CORS middleware for enterprise Angular frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

