from fastapi import APIRouter
from .analyze import router as analyze_router
from .report import router as report_router
from .visualize import router as visualize_router

router = APIRouter()
router.include_router(analyze_router)
router.include_router(report_router)
router.include_router(visualize_router)

__all__ = ["router"]
