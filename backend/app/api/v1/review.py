from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import AppSettings, get_settings
from app.db.dependencies import get_db
from app.models import Application, RubricCriterionScore
from app.security import AuthenticatedActor, READ_ROLES, WRITE_ROLES, actor_with_id, require_roles
from app.schemas.review import (
    CriterionOverrideRequest,
    CriterionOverrideResponse,
    FinalDecisionRequest,
    FinalDecisionResponse,
    ReviewerApplicationDetail,
    ReviewerApplicationListItem,
    ReviewerNotesRequest,
    ReviewerNotesResponse,
)
from app.services.review import (
    attach_scorecard_override,
    build_reviewer_application_detail,
    list_reviewer_applications,
    record_audit_action,
    score_override_payload,
)
from app.services.audit import AuditAction, record_audit_log


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/review", tags=["review"])


@router.get("/applications", response_model=list[ReviewerApplicationListItem])
def list_applications_for_review(
    db: Session = Depends(get_db),
    actor: AuthenticatedActor = Depends(require_roles(*READ_ROLES)),
) -> list[ReviewerApplicationListItem]:
    _ = actor
    return list_reviewer_applications(db=db)


@router.get("/applications/{application_id}", response_model=ReviewerApplicationDetail)
def get_application_for_review(
    application_id: str,
    db: Session = Depends(get_db),
    settings: AppSettings = Depends(get_settings),
    actor: AuthenticatedActor = Depends(require_roles(*READ_ROLES)),
) -> ReviewerApplicationDetail:
    _ = actor
    application = _get_application_or_404(db, application_id)
    return build_reviewer_application_detail(application=application, settings=settings)


@router.post(
    "/applications/{application_id}/criterion/{criterion_id}/override",
    response_model=CriterionOverrideResponse,
)
def override_criterion_score(
    application_id: str,
    criterion_id: str,
    payload: CriterionOverrideRequest,
    db: Session = Depends(get_db),
    actor: AuthenticatedActor = Depends(require_roles(*WRITE_ROLES)),
) -> CriterionOverrideResponse:
    application = _get_application_or_404(db, application_id)
    score = db.scalar(
        select(RubricCriterionScore).where(
            RubricCriterionScore.application_id == application_id,
            RubricCriterionScore.criterion_id == criterion_id,
        )
    )
    if score is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rubric criterion score not found.",
        )
    if payload.score is not None and payload.score > score.max_score:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Override score cannot exceed criterion max_score.",
        )

    old_value = {
        "criterion_id": criterion_id,
        "score": score.score,
        "reviewer_override": score.reviewer_override,
    }
    reviewer_id = payload.reviewer_id or actor.actor_id
    audit_actor = actor_with_id(actor, reviewer_id)
    override_payload = score_override_payload(
        score=score,
        reason=payload.reason,
        reviewer_id=reviewer_id,
        override_score=payload.score,
        rationale=payload.rationale,
        requires_human_review=payload.requires_human_review,
    )
    score.reviewer_override = override_payload
    attach_scorecard_override(
        scorecard=application.scorecard,
        criterion_id=criterion_id,
        override_payload=override_payload,
    )
    record_audit_action(
        application,
        action="criterion_override_recorded",
        reviewer_id=reviewer_id,
        metadata={
            "criterion_id": criterion_id,
            "reason_present": True,
            "ai_score_preserved": True,
        },
    )
    record_audit_log(
        db=db,
        actor=audit_actor,
        action=AuditAction.SCORE_OVERRIDE,
        application_id=application_id,
        old_value=old_value,
        new_value={
            "criterion_id": criterion_id,
            "override_score": payload.score,
            "requires_human_review": payload.requires_human_review,
            "reason_present": True,
            "rationale_present": payload.rationale is not None,
        },
        metadata={
            "ai_score_preserved": True,
            "reviewer_id": reviewer_id,
        },
    )
    db.commit()
    db.refresh(score)
    db.refresh(application)
    logger.info("Recorded reviewer override for application %s criterion %s", application_id, criterion_id)
    return CriterionOverrideResponse(
        application_id=application_id,
        criterion_id=criterion_id,
        reviewer_override=score.reviewer_override or {},
        audit_metadata=application.reviewer_audit_metadata or [],
    )


@router.post("/applications/{application_id}/decision", response_model=FinalDecisionResponse)
def record_final_human_decision(
    application_id: str,
    payload: FinalDecisionRequest,
    db: Session = Depends(get_db),
    actor: AuthenticatedActor = Depends(require_roles(*WRITE_ROLES)),
) -> FinalDecisionResponse:
    application = _get_application_or_404(db, application_id)
    old_decision = application.final_human_decision
    reviewer_id = payload.reviewer_id or actor.actor_id
    audit_actor = actor_with_id(actor, reviewer_id)
    application.final_human_decision = payload.decision
    application.review_status = "decision_recorded"
    record_audit_action(
        application,
        action="final_human_decision_recorded",
        reviewer_id=reviewer_id,
        metadata={
            "decision": payload.decision,
            "reason_present": payload.reason is not None,
            "ai_recommendation_preserved": True,
            "stored_separately_from_ai_scorecard": True,
        },
    )
    record_audit_log(
        db=db,
        actor=audit_actor,
        action=AuditAction.FINAL_DECISION,
        application_id=application_id,
        old_value={"final_human_decision": old_decision},
        new_value={"final_human_decision": payload.decision},
        metadata={
            "reason_present": payload.reason is not None,
            "ai_recommendation_preserved": True,
            "stored_separately_from_ai_scorecard": True,
            "reviewer_id": reviewer_id,
        },
    )
    db.commit()
    db.refresh(application)
    logger.info("Recorded final human decision for application %s", application_id)
    return FinalDecisionResponse(
        application_id=application_id,
        final_human_decision=payload.decision,
        audit_metadata=application.reviewer_audit_metadata or [],
        ai_recommendation_is_final_decision=False,
    )


@router.post("/applications/{application_id}/notes", response_model=ReviewerNotesResponse)
def record_reviewer_notes(
    application_id: str,
    payload: ReviewerNotesRequest,
    db: Session = Depends(get_db),
    actor: AuthenticatedActor = Depends(require_roles(*WRITE_ROLES)),
) -> ReviewerNotesResponse:
    application = _get_application_or_404(db, application_id)
    application.reviewer_notes = payload.notes
    reviewer_id = payload.reviewer_id or actor.actor_id
    record_audit_action(
        application,
        action="reviewer_notes_updated",
        reviewer_id=reviewer_id,
        metadata={"notes_present": True},
    )
    db.commit()
    db.refresh(application)
    logger.info("Recorded reviewer notes for application %s", application_id)
    return ReviewerNotesResponse(
        application_id=application_id,
        reviewer_notes=application.reviewer_notes or "",
        audit_metadata=application.reviewer_audit_metadata or [],
    )


def _get_application_or_404(db: Session, application_id: str) -> Application:
    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application not found.",
        )
    return application
