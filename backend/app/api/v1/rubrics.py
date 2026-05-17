from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.schemas.rubrics import AdmissionsRubric
from app.services.rubrics import RubricLoadError, load_default_rubric


router = APIRouter(prefix="/rubrics", tags=["rubrics"])


@router.get("/default", response_model=AdmissionsRubric)
def get_default_rubric() -> AdmissionsRubric:
    try:
        return load_default_rubric()
    except RubricLoadError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Default rubric is not configured correctly.",
        ) from exc
