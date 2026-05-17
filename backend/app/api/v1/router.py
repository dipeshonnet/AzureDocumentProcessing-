from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.applications import router as applications_router
from app.api.v1.documents import router as documents_router
from app.api.v1.review import router as review_router
from app.api.v1.rubrics import router as rubrics_router
from app.api.v1.system import router as system_router


router = APIRouter()
router.include_router(applications_router)
router.include_router(documents_router)
router.include_router(review_router)
router.include_router(rubrics_router)
router.include_router(system_router, prefix="/system", tags=["system"])
