from __future__ import annotations

import logging
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import AppSettings, get_settings
from app.db.dependencies import get_db
from app.models import Applicant, Application, ApplicationDocument, RubricCriterionScore, RubricScorecard
from app.security import AuthenticatedActor, UserRole, require_roles
from app.schemas.admissions import (
    ApplicationProcessingResultRead,
    ApplicationProcessingStatusRead,
    ApplicationRubricScoreRead,
    ApplicationCreate,
    ApplicationDocumentRead,
    ApplicationRead,
    RubricCriterionScoreRead,
    RubricScorecardRead,
)
from app.services.rubric_scoring import (
    RubricScoringApplicationNotFoundError,
    RubricScoringService,
    RubricScoringServiceError,
    get_rubric_scoring_service,
    replace_persisted_scores,
)
from app.services.application_processing import (
    ApplicationProcessingApplicationNotFoundError,
    ApplicationProcessingConfigurationError,
    ApplicationProcessingNoDocumentsError,
    ApplicationProcessingOutcome,
    ApplicationProcessingService,
)
from app.services.audit import AuditAction, record_audit_log
from app.services.rubrics import RubricLoadError, load_default_rubric
from app.services.storage import (
    StorageError,
    StorageService,
    build_document_storage_path,
    get_storage_service,
    safe_filename,
)
from app.services.upload_validation import read_upload_with_limit, validate_upload_filename


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/applications", tags=["applications"])


def storage_service_dependency(
    settings: AppSettings = Depends(get_settings),
) -> StorageService:
    try:
        return get_storage_service(settings)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Storage backend is not configured correctly.",
        ) from exc


def rubric_scoring_service_dependency(
    db: Session = Depends(get_db),
    settings: AppSettings = Depends(get_settings),
) -> RubricScoringService:
    try:
        return get_rubric_scoring_service(db=db, settings=settings)
    except RubricScoringServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Rubric scoring service is not configured.",
        ) from exc


def application_processing_service_dependency(
    db: Session = Depends(get_db),
    settings: AppSettings = Depends(get_settings),
) -> ApplicationProcessingService:
    try:
        return ApplicationProcessingService(db=db, settings=settings)
    except ApplicationProcessingConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Application processing services are not configured.",
        ) from exc


def _get_application_or_404(db: Session, application_id: str) -> Application:
    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application not found.",
        )
    return application


@router.post("", response_model=ApplicationRead, status_code=status.HTTP_201_CREATED)
def create_application(
    payload: ApplicationCreate,
    db: Session = Depends(get_db),
    actor: AuthenticatedActor = Depends(require_roles(*(
        UserRole.ADMIN,
        UserRole.ADMISSIONS_REVIEWER,
    ))),
) -> Application:
    if payload.final_human_decision is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Final human decisions cannot be set by the application creation endpoint.",
        )

    applicant = db.get(Applicant, payload.applicant_id)
    if applicant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Applicant not found.",
        )

    application = Application(
        applicant_id=payload.applicant_id,
        submitted_at=payload.submitted_at,
        processing_status=payload.processing_status,
        review_status=payload.review_status,
        final_human_decision=None,
        reviewer_notes=payload.reviewer_notes,
        processing_errors=[],
        reviewer_audit_metadata=[],
    )
    db.add(application)
    db.flush()
    record_audit_log(
        db=db,
        actor=actor,
        action="application_create",
        application_id=application.application_id,
        new_value={
            "applicant_id": payload.applicant_id,
            "processing_status": payload.processing_status,
            "review_status": payload.review_status,
        },
    )
    db.commit()
    db.refresh(application)

    logger.info("Created application metadata record %s", application.application_id)
    return application


@router.get("/{application_id}", response_model=ApplicationRead)
def get_application(
    application_id: str,
    db: Session = Depends(get_db),
    actor: AuthenticatedActor = Depends(require_roles(*(
        UserRole.ADMIN,
        UserRole.ADMISSIONS_REVIEWER,
        UserRole.READ_ONLY_AUDITOR,
    ))),
) -> Application:
    _ = actor
    return _get_application_or_404(db, application_id)


@router.post(
    "/{application_id}/documents",
    response_model=ApplicationDocumentRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    application_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    settings: AppSettings = Depends(get_settings),
    storage_service: StorageService = Depends(storage_service_dependency),
    actor: AuthenticatedActor = Depends(require_roles(*(
        UserRole.ADMIN,
        UserRole.ADMISSIONS_REVIEWER,
    ))),
) -> ApplicationDocument:
    _get_application_or_404(db, application_id)
    original_filename = validate_upload_filename(file.filename)
    content = await read_upload_with_limit(file, max_bytes=settings.max_upload_bytes)

    document_id = str(uuid4())
    cleaned_filename = safe_filename(original_filename)
    storage_path = build_document_storage_path(
        application_id=application_id,
        document_id=document_id,
        filename=cleaned_filename,
    )

    try:
        blob_url_or_path = storage_service.save_bytes(
            storage_path=storage_path,
            content=content,
        )
    except NotImplementedError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Configured storage backend is not implemented yet.",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Configured storage path is not valid.",
        ) from exc
    except StorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Document storage failed.",
        ) from exc

    document = ApplicationDocument(
        document_id=document_id,
        application_id=application_id,
        original_filename=cleaned_filename,
        blob_url_or_path=blob_url_or_path,
        document_type=None,
        classification_confidence=None,
        processing_status="uploaded",
        page_count=None,
    )
    db.add(document)
    record_audit_log(
        db=db,
        actor=actor,
        action=AuditAction.DOCUMENT_UPLOAD,
        application_id=application_id,
        document_id=document_id,
        new_value={
            "original_filename": cleaned_filename,
            "blob_url_or_path": blob_url_or_path,
            "storage_path_pattern": "applications/{application_id}/raw/{document_id}/{safe_filename}",
            "file_size_bytes": len(content),
            "processing_status": "uploaded",
        },
    )
    db.commit()
    db.refresh(document)

    logger.info("Stored upload metadata for document %s on application %s", document_id, application_id)
    return document


@router.get("/{application_id}/documents", response_model=list[ApplicationDocumentRead])
def list_application_documents(
    application_id: str,
    db: Session = Depends(get_db),
    actor: AuthenticatedActor = Depends(require_roles(*(
        UserRole.ADMIN,
        UserRole.ADMISSIONS_REVIEWER,
        UserRole.READ_ONLY_AUDITOR,
    ))),
) -> list[ApplicationDocument]:
    _ = actor
    _get_application_or_404(db, application_id)
    return list(
        db.scalars(
            select(ApplicationDocument)
            .where(ApplicationDocument.application_id == application_id)
            .order_by(ApplicationDocument.created_at)
        )
    )


@router.post("/{application_id}/process", response_model=ApplicationProcessingResultRead)
def process_application(
    application_id: str,
    force: bool = Query(default=False),
    processing_service: ApplicationProcessingService = Depends(application_processing_service_dependency),
    actor: AuthenticatedActor = Depends(require_roles(*(
        UserRole.ADMIN,
        UserRole.ADMISSIONS_REVIEWER,
    ))),
) -> ApplicationProcessingResultRead:
    try:
        outcome = processing_service.process_application(application_id, force=force, actor=actor)
    except ApplicationProcessingApplicationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application not found.",
        ) from exc
    except ApplicationProcessingNoDocumentsError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Application has no uploaded documents to process.",
        ) from exc

    return _processing_result_response(outcome)


@router.get("/{application_id}/processing-status", response_model=ApplicationProcessingStatusRead)
def get_processing_status(
    application_id: str,
    processing_service: ApplicationProcessingService = Depends(application_processing_service_dependency),
    actor: AuthenticatedActor = Depends(require_roles(*(
        UserRole.ADMIN,
        UserRole.ADMISSIONS_REVIEWER,
        UserRole.READ_ONLY_AUDITOR,
    ))),
) -> ApplicationProcessingStatusRead:
    _ = actor
    try:
        outcome = processing_service.status_for_application(application_id)
    except ApplicationProcessingApplicationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application not found.",
        ) from exc
    return _processing_status_response(outcome)


@router.post("/{application_id}/score", response_model=ApplicationRubricScoreRead)
def score_application(
    application_id: str,
    force: bool = Query(default=False),
    db: Session = Depends(get_db),
    scoring_service: RubricScoringService = Depends(rubric_scoring_service_dependency),
    actor: AuthenticatedActor = Depends(require_roles(*(
        UserRole.ADMIN,
        UserRole.ADMISSIONS_REVIEWER,
    ))),
) -> ApplicationRubricScoreRead:
    _get_application_or_404(db, application_id)

    existing_scorecard = db.get(RubricScorecard, application_id)
    existing_scores = _score_rows_for_application(db, application_id)
    if existing_scorecard is not None and existing_scores and not force:
        return _score_response(
            application_id=application_id,
            scores=existing_scores,
            scorecard=existing_scorecard,
        )

    try:
        outcome = scoring_service.score_application(application_id)
        rubric = load_default_rubric()
    except RubricScoringApplicationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application not found.",
        ) from exc
    except (RubricScoringServiceError, RubricLoadError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Application rubric scoring failed.",
        ) from exc

    old_scorecard = (
        {
            "weighted_score": existing_scorecard.weighted_score,
            "recommendation_band": existing_scorecard.recommendation_band,
            "requires_human_review": existing_scorecard.requires_human_review,
        }
        if existing_scorecard is not None
        else None
    )
    scores, scorecard = replace_persisted_scores(
        db=db,
        application_id=application_id,
        outcome=outcome,
        rubric=rubric,
    )
    record_audit_log(
        db=db,
        actor=actor,
        action=AuditAction.APPLICATION_SCORING,
        application_id=application_id,
        old_value=old_scorecard,
        new_value={
            "weighted_score": scorecard.weighted_score,
            "max_score": scorecard.max_score,
            "recommendation_band": scorecard.recommendation_band,
            "requires_human_review": scorecard.requires_human_review,
            "criterion_count": len(scores),
        },
    )
    db.commit()
    logger.info("Persisted rubric scoring metadata for application %s", application_id)
    return _score_response(application_id=application_id, scores=scores, scorecard=scorecard)


def _score_rows_for_application(db: Session, application_id: str) -> list[RubricCriterionScore]:
    return list(
        db.scalars(
            select(RubricCriterionScore)
            .where(RubricCriterionScore.application_id == application_id)
            .order_by(RubricCriterionScore.criterion_id)
        )
    )


def _score_response(
    *,
    application_id: str,
    scores: list[RubricCriterionScore],
    scorecard: RubricScorecard,
) -> ApplicationRubricScoreRead:
    return ApplicationRubricScoreRead(
        application_id=application_id,
        weighted_score=scorecard.weighted_score,
        max_score=scorecard.max_score,
        recommendation_band=scorecard.recommendation_band,
        decision_support_summary=scorecard.decision_support_summary or "",
        requires_human_review=scorecard.requires_human_review,
        risk_flags=scorecard.risk_flags,
        criteria=[RubricCriterionScoreRead.model_validate(score) for score in scores],
        scorecard=RubricScorecardRead.model_validate(scorecard),
    )


def _processing_status_response(
    outcome: ApplicationProcessingOutcome,
) -> ApplicationProcessingStatusRead:
    return ApplicationProcessingStatusRead(
        application_id=outcome.application.application_id,
        processing_status=outcome.application.processing_status,
        review_status=outcome.application.review_status,
        processing_errors=outcome.application.processing_errors,
        documents=[ApplicationDocumentRead.model_validate(document) for document in outcome.documents],
        scorecard=(
            RubricScorecardRead.model_validate(outcome.scorecard)
            if outcome.scorecard is not None
            else None
        ),
    )


def _processing_result_response(
    outcome: ApplicationProcessingOutcome,
) -> ApplicationProcessingResultRead:
    scorecard_detail = None
    if outcome.scorecard is not None and outcome.score_rows:
        scorecard_detail = _score_response(
            application_id=outcome.application.application_id,
            scores=outcome.score_rows,
            scorecard=outcome.scorecard,
        )
    return ApplicationProcessingResultRead(
        **_processing_status_response(outcome).model_dump(),
        scorecard_detail=scorecard_detail,
    )
