from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import AppSettings
from app.models import Application, ApplicationDocument, RubricCriterionScore, RubricScorecard
from app.schemas.admissions import (
    ApplicantRead,
    ApplicationDocumentRead,
    ApplicationRead,
    DocumentSummaryRead,
    ExtractedDocumentContentRead,
    RubricCriterionScoreRead,
    RubricScorecardRead,
)
from app.schemas.review import ReviewerApplicationDetail, ReviewerApplicationListItem, ReviewerDocumentDetail
from app.services.audit import sanitize_audit_value
from app.services.prompts import load_prompt


PROMPT_NAMES: tuple[str, ...] = (
    "document_classification",
    "transcript_extraction",
    "personal_statement_extraction",
    "recommendation_extraction",
    "resume_extraction",
    "test_score_extraction",
    "document_summary",
    "rubric_scoring",
)


@lru_cache
def prompt_version_map() -> dict[str, str]:
    return {
        prompt_name: load_prompt(prompt_name).prompt_version
        for prompt_name in PROMPT_NAMES
    }


def list_reviewer_applications(
    *,
    db: Session,
) -> list[ReviewerApplicationListItem]:
    applications = list(
        db.scalars(
            select(Application).join(Application.applicant).order_by(Application.application_id)
        )
    )
    return [reviewer_application_list_item(application) for application in applications]


def reviewer_application_list_item(application: Application) -> ReviewerApplicationListItem:
    scorecard = application.scorecard
    return ReviewerApplicationListItem(
        application_id=application.application_id,
        applicant=ApplicantRead.model_validate(application.applicant),
        submitted_at=application.submitted_at,
        processing_status=application.processing_status,
        review_status=application.review_status,
        final_human_decision=application.final_human_decision,
        weighted_score=scorecard.weighted_score if scorecard else None,
        max_score=scorecard.max_score if scorecard else None,
        recommendation_band=scorecard.recommendation_band if scorecard else None,
        requires_human_review=application_requires_human_review(application),
        document_count=len(application.documents),
    )


def build_reviewer_application_detail(
    *,
    application: Application,
    settings: AppSettings,
) -> ReviewerApplicationDetail:
    documents = [
        reviewer_document_detail(document=document, settings=settings)
        for document in sorted(application.documents, key=lambda item: item.created_at)
    ]
    scores = sorted(application.rubric_scores, key=lambda score: score.criterion_id)
    scorecard = application.scorecard
    return ReviewerApplicationDetail(
        application=ApplicationRead.model_validate(application),
        applicant=ApplicantRead.model_validate(application.applicant),
        documents=documents,
        rubric_criterion_scores=[RubricCriterionScoreRead.model_validate(score) for score in scores],
        supporting_evidence=supporting_evidence_for_scores(scores),
        confidence=confidence_summary(application),
        human_review_flags=human_review_flags(application),
        weighted_score=scorecard.weighted_score if scorecard else None,
        max_score=scorecard.max_score if scorecard else None,
        scorecard=RubricScorecardRead.model_validate(scorecard) if scorecard else None,
        model_versions=application_model_versions(settings=settings, scorecard=scorecard),
        prompt_versions=prompt_version_map(),
        reviewer_overrides=reviewer_overrides(application),
        final_human_decision=application.final_human_decision,
        audit_metadata=application.reviewer_audit_metadata or [],
        ai_recommendation_is_final_decision=False,
        decision_support_only=True,
    )


def reviewer_document_detail(
    *,
    document: ApplicationDocument,
    settings: AppSettings,
) -> ReviewerDocumentDetail:
    extracted = document.extracted_content
    summary = document.summary
    structured = extracted.structured_extraction if extracted else {}
    return ReviewerDocumentDetail(
        document=ApplicationDocumentRead.model_validate(document),
        classification={
            "document_type": document.document_type,
            "confidence": document.classification_confidence,
            "metadata": document.classification_metadata or {},
        },
        extraction=ExtractedDocumentContentRead.model_validate(extracted) if extracted else None,
        structured_extracted_fields=structured or {},
        summary=DocumentSummaryRead.model_validate(summary) if summary else None,
        model_versions=document_model_versions(document=document, settings=settings),
        prompt_versions={
            "document_classification": prompt_version_map()["document_classification"],
            "document_summary": prompt_version_map()["document_summary"],
            **structured_prompt_version_for_document(document),
        },
        human_review_flags=document_human_review_flags(document),
    )


def record_audit_action(
    application: Application,
    *,
    action: str,
    reviewer_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record = {
        "action": action,
        "reviewer_id": reviewer_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "metadata": sanitize_audit_value(metadata or {}),
        "ai_decision_support_only": True,
    }
    application.reviewer_audit_metadata = [
        *(application.reviewer_audit_metadata or []),
        record,
    ]
    return record


def score_override_payload(
    *,
    score: RubricCriterionScore,
    reason: str,
    reviewer_id: str | None,
    override_score: float | None,
    rationale: str | None,
    requires_human_review: bool | None,
) -> dict[str, Any]:
    return {
        "criterion_id": score.criterion_id,
        "reason": reason,
        "reviewer_id": reviewer_id,
        "score": override_score,
        "rationale": rationale,
        "requires_human_review": requires_human_review,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": "human_reviewer",
        "audit_metadata": {
            "action": "criterion_override_recorded",
            "ai_score_preserved": True,
            "ai_decision_support_only": True,
        },
    }


def attach_scorecard_override(
    *,
    scorecard: RubricScorecard | None,
    criterion_id: str,
    override_payload: dict[str, Any],
) -> None:
    if scorecard is None:
        return
    existing = dict(scorecard.reviewer_override or {})
    criteria = dict(existing.get("criteria") or {})
    criteria[criterion_id] = override_payload
    existing["criteria"] = criteria
    existing["updated_at"] = datetime.now(timezone.utc).isoformat()
    existing["ai_recommendation_preserved"] = True
    scorecard.reviewer_override = existing


def application_requires_human_review(application: Application) -> bool:
    if application.processing_status in {"failed", "needs_manual_review"}:
        return True
    if application.scorecard and application.scorecard.requires_human_review:
        return True
    return any(document_human_review_flags(document) for document in application.documents)


def human_review_flags(application: Application) -> list[str]:
    flags: set[str] = set()
    if application.processing_errors:
        flags.add("processing_errors_present")
    if application.scorecard and application.scorecard.requires_human_review:
        flags.add("scorecard_requires_human_review")
    if application.scorecard:
        flags.update(application.scorecard.risk_flags or [])
    for score in application.rubric_scores:
        if score.requires_human_review:
            flags.add(f"criterion_requires_human_review:{score.criterion_id}")
        flags.update(score.risk_flags or [])
    for document in application.documents:
        flags.update(document_human_review_flags(document))
    return sorted(flags)


def document_human_review_flags(document: ApplicationDocument) -> list[str]:
    flags: set[str] = set()
    classification_metadata = document.classification_metadata or {}
    if classification_metadata.get("requires_human_review") is True:
        flags.add("classification_requires_human_review")
    if document.classification_confidence is not None and document.classification_confidence < 0.65:
        flags.add("low_classification_confidence")
    if document.extracted_content and document.extracted_content.extraction_confidence is not None:
        if document.extracted_content.extraction_confidence < 0.5:
            flags.add("low_extraction_confidence")
    structured_metadata = (
        (document.extracted_content.structured_extraction or {}).get("metadata", {})
        if document.extracted_content
        else {}
    )
    if structured_metadata.get("requires_human_review") is True:
        flags.add("structured_extraction_requires_human_review")
    if document.summary:
        if document.summary.concerns:
            flags.add("summary_concerns_present")
        if document.summary.missing_information:
            flags.add("summary_missing_information_present")
    if document.processing_status == "failed":
        flags.add("document_processing_failed")
    return sorted(flags)


def structured_prompt_version_for_document(document: ApplicationDocument) -> dict[str, str]:
    extracted = document.extracted_content
    metadata = (
        (extracted.structured_extraction or {}).get("metadata", {})
        if extracted
        else {}
    )
    prompt_name = metadata.get("prompt_name")
    prompt_version = metadata.get("prompt_version")
    if isinstance(prompt_name, str) and isinstance(prompt_version, str):
        return {prompt_name: prompt_version}
    return {
        name: version
        for name, version in prompt_version_map().items()
        if name.endswith("_extraction") and name != "document_classification"
    }


def supporting_evidence_for_scores(scores: list[RubricCriterionScore]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for score in scores:
        for item in score.supporting_evidence or []:
            evidence.append(
                {
                    "criterion_id": score.criterion_id,
                    "criterion_name": score.criterion_name,
                    **item,
                }
            )
    return evidence


def confidence_summary(application: Application) -> dict[str, Any]:
    criterion_confidences = [
        score.confidence
        for score in application.rubric_scores
        if score.confidence is not None
    ]
    documents = {
        document.document_id: {
            "classification_confidence": document.classification_confidence,
            "extraction_confidence": (
                document.extracted_content.extraction_confidence
                if document.extracted_content
                else None
            ),
        }
        for document in application.documents
    }
    average = (
        round(sum(criterion_confidences) / len(criterion_confidences), 4)
        if criterion_confidences
        else None
    )
    return {
        "average_rubric_confidence": average,
        "criterion_confidences": {
            score.criterion_id: score.confidence for score in application.rubric_scores
        },
        "documents": documents,
    }


def reviewer_overrides(application: Application) -> dict[str, Any]:
    criterion_overrides = {
        score.criterion_id: score.reviewer_override
        for score in application.rubric_scores
        if score.reviewer_override
    }
    return {
        "criteria": criterion_overrides,
        "scorecard": application.scorecard.reviewer_override if application.scorecard else None,
    }


def application_model_versions(
    *,
    settings: AppSettings,
    scorecard: RubricScorecard | None,
) -> dict[str, Any]:
    return {
        "document_extraction_backend": settings.document_extraction_backend,
        "document_classification_backend": settings.document_classification_backend,
        "structured_extraction_backend": settings.structured_extraction_backend,
        "document_summarization_backend": settings.document_summarization_backend,
        "rubric_scoring_backend": settings.rubric_scoring_backend,
        "azure_openai_classification_model_deployment": settings.azure_openai_classification_model_deployment,
        "azure_openai_structured_extraction_model_deployment": settings.azure_openai_structured_extraction_model_deployment,
        "azure_openai_summarization_model_deployment": settings.azure_openai_summarization_model_deployment,
        "azure_openai_rubric_scoring_model_deployment": settings.azure_openai_rubric_scoring_model_deployment,
        "scorecard_created_at": scorecard.created_at.isoformat() if scorecard else None,
    }


def document_model_versions(
    *,
    document: ApplicationDocument,
    settings: AppSettings,
) -> dict[str, Any]:
    extracted = document.extracted_content
    structured_metadata = (
        (extracted.structured_extraction or {}).get("metadata", {})
        if extracted
        else {}
    )
    return {
        "extraction": extracted.extraction_metadata if extracted else {},
        "classification": document.classification_metadata or {},
        "structured_extraction": structured_metadata,
        "summarization": {
            "backend": settings.document_summarization_backend,
            "model_deployment": settings.azure_openai_summarization_model_deployment,
        },
    }
