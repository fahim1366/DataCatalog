from fastapi import APIRouter
from backend.src.api.v1.audit_logs import router as audit_logs_router
from backend.src.api.v1.connections import router as connections_router
from backend.src.api.v1.ingestion import router as ingestion_router
from backend.src.api.v1.permissions import router as permissions_router
from backend.src.api.v1.reports import router as reports_router
from backend.src.api.v1.search import router as search_router
from backend.src.api.v1.tables import router as tables_router
from backend.src.api.v1.tags import router as tags_router
from backend.src.api.v1.versions import router as versions_router

api_v1_router = APIRouter()
api_v1_router.include_router(audit_logs_router)
api_v1_router.include_router(connections_router)
api_v1_router.include_router(ingestion_router)
api_v1_router.include_router(permissions_router)
api_v1_router.include_router(reports_router)
api_v1_router.include_router(search_router)
api_v1_router.include_router(tables_router)
api_v1_router.include_router(tags_router)
api_v1_router.include_router(versions_router)

__all__ = ["api_v1_router"]
