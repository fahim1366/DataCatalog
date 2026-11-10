from fastapi import APIRouter
from backend.src.api.v1.search import router as search_router
from backend.src.api.v1.tables import router as tables_router

api_v1_router = APIRouter()
api_v1_router.include_router(search_router)
api_v1_router.include_router(tables_router)

__all__ = ["api_v1_router"]

