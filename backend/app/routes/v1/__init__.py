from fastapi import APIRouter
from .validate import router as validate_router
from .crawl import router as crawl_router
from .assess import router as assess_router
from .rca import router as rca_router

router = APIRouter(prefix="/v1", tags=["Hub v1"])

router.include_router(validate_router)
router.include_router(crawl_router)
router.include_router(assess_router)
router.include_router(rca_router)
