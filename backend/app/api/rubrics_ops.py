from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.dependencies import get_db
from app.schemas.intake import SavedRubricCreate, SavedRubricRead, SavedRubricUpdate
from app.security import UserRole
from app.services.audit import AuditAction, record_audit_log
from app.services.local_auth import AuthenticatedUser, require_local_roles
from app.services.rubric_store import (
    create_saved_rubric,
    get_saved_rubric,
    list_saved_rubrics,
    saved_rubric_to_read,
    update_saved_rubric,
)


router = APIRouter(prefix="/api/rubrics", tags=["operations-rubrics"])


@router.get("", response_model=list[SavedRubricRead])
def list_rubrics(
    db: Session = Depends(get_db),
    authenticated: AuthenticatedUser = Depends(
        require_local_roles(UserRole.ADMIN, UserRole.ADMISSIONS_REVIEWER, UserRole.READ_ONLY_AUDITOR)
    ),
) -> list[SavedRubricRead]:
    _ = authenticated
    return [saved_rubric_to_read(rubric) for rubric in list_saved_rubrics(db)]


@router.get("/{rubric_id}", response_model=SavedRubricRead)
def get_rubric(
    rubric_id: str,
    db: Session = Depends(get_db),
    authenticated: AuthenticatedUser = Depends(
        require_local_roles(UserRole.ADMIN, UserRole.ADMISSIONS_REVIEWER, UserRole.READ_ONLY_AUDITOR)
    ),
) -> SavedRubricRead:
    _ = authenticated
    rubric = get_saved_rubric(db, rubric_id)
    if rubric is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rubric not found.")
    return saved_rubric_to_read(rubric)


@router.post("", response_model=SavedRubricRead, status_code=status.HTTP_201_CREATED)
def create_rubric(
    payload: SavedRubricCreate,
    db: Session = Depends(get_db),
    authenticated: AuthenticatedUser = Depends(
        require_local_roles(UserRole.ADMIN, UserRole.ADMISSIONS_REVIEWER)
    ),
) -> SavedRubricRead:
    rubric = create_saved_rubric(db, payload)
    record_audit_log(
        db=db,
        actor=authenticated.actor,
        action=AuditAction.RUBRIC_SAVE,
        new_value={
            "rubric_id": rubric.rubric_id,
            "name": rubric.name,
            "section_count": len(rubric.sections),
            "total_points": rubric.total_points,
        },
    )
    db.commit()
    db.refresh(rubric)
    return saved_rubric_to_read(rubric)


@router.put("/{rubric_id}", response_model=SavedRubricRead)
def update_rubric(
    rubric_id: str,
    payload: SavedRubricUpdate,
    db: Session = Depends(get_db),
    authenticated: AuthenticatedUser = Depends(
        require_local_roles(UserRole.ADMIN, UserRole.ADMISSIONS_REVIEWER)
    ),
) -> SavedRubricRead:
    rubric = get_saved_rubric(db, rubric_id)
    if rubric is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rubric not found.")
    old_value = {
        "name": rubric.name,
        "section_count": len(rubric.sections),
        "total_points": rubric.total_points,
        "version": rubric.version,
    }
    rubric = update_saved_rubric(db, rubric, payload)
    record_audit_log(
        db=db,
        actor=authenticated.actor,
        action=AuditAction.RUBRIC_SAVE,
        old_value=old_value,
        new_value={
            "rubric_id": rubric.rubric_id,
            "name": rubric.name,
            "section_count": len(rubric.sections),
            "total_points": rubric.total_points,
            "version": rubric.version,
        },
    )
    db.commit()
    db.refresh(rubric)
    return saved_rubric_to_read(rubric)
