from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import AppSettings
from app.models import (
    Application,
    ApplicationDocument,
    DocumentSummary,
    ExtractedDocumentContent,
    RubricCriterionScore,
    RubricScorecard,
)
from app.security import AuthenticatedActor, system_actor
from app.services.audit import AuditAction, record_audit_log
from app.services.classification import (
    ClassificationDocumentNotFoundError,
    ClassificationResult,
    ClassificationServiceError,
    TwoStageClassificationService,
    document_classification_prompt_metadata,
    get_document_classification_service,
)
from app.services.document_extraction import (
    AzureDocumentExtractionError,
    DocumentExtractionService,
    DocumentNotFoundError,
    EmptyOcrResultError,
    ExtractionTimeoutError,
    UnsupportedDocumentTypeError,
    get_document_extraction_service,
    resolved_document_page_count,
)
from app.services.rubric_scoring import (
    RubricScoringApplicationNotFoundError,
    RubricScoringService,
    RubricScoringServiceError,
    get_rubric_scoring_service,
    replace_persisted_scores,
)
from app.services.structured_extraction import (
    StructuredExtractionDocumentNotFoundError,
    StructuredExtractionRequiresOcrError,
    StructuredExtractionService,
    StructuredExtractionServiceError,
    UnsupportedStructuredExtractionTypeError,
    get_structured_extraction_service,
)
from app.services.summarization import (
    SummarizationDocumentNotFoundError,
    SummarizationRequiresOcrError,
    SummarizationService,
    SummarizationServiceError,
    get_summarization_service,
    summary_result_to_model,
)


logger = logging.getLogger(__name__)


PROCESSING_STATUS_PENDING = "pending"
PROCESSING_STATUS_EXTRACTING = "extracting"
PROCESSING_STATUS_CLASSIFYING = "classifying"
PROCESSING_STATUS_SUMMARIZING = "summarizing"
PROCESSING_STATUS_SCORING = "scoring"
PROCESSING_STATUS_READY_FOR_REVIEW = "ready_for_review"
PROCESSING_STATUS_FAILED = "failed"
PROCESSING_STATUS_NEEDS_MANUAL_REVIEW = "needs_manual_review"

PROCESSING_STATUSES: frozenset[str] = frozenset(
    {
        PROCESSING_STATUS_PENDING,
        PROCESSING_STATUS_EXTRACTING,
        PROCESSING_STATUS_CLASSIFYING,
        PROCESSING_STATUS_SUMMARIZING,
        PROCESSING_STATUS_SCORING,
        PROCESSING_STATUS_READY_FOR_REVIEW,
        PROCESSING_STATUS_FAILED,
        PROCESSING_STATUS_NEEDS_MANUAL_REVIEW,
    }
)


class ApplicationProcessingError(Exception):
    """Base error for application processing failures."""


class ApplicationProcessingApplicationNotFoundError(ApplicationProcessingError):
    pass


class ApplicationProcessingNoDocumentsError(ApplicationProcessingError):
    pass


class ApplicationProcessingConfigurationError(ApplicationProcessingError):
    pass


@dataclass(frozen=True)
class ApplicationProcessingOutcome:
    application: Application
    documents: list[ApplicationDocument]
    score_rows: list[RubricCriterionScore]
    scorecard: RubricScorecard | None


class ApplicationProcessingService:
    """Orchestrates the admissions document processing pipeline.

    Each document-level step is idempotent by default and reuses previously
    persisted output unless the caller explicitly asks for a force refresh.
    """

    def __init__(
        self,
        *,
        db: Session,
        settings: AppSettings,
        extraction_service: DocumentExtractionService | None = None,
        classification_service: TwoStageClassificationService | None = None,
        structured_extraction_service: StructuredExtractionService | None = None,
        summarization_service: SummarizationService | None = None,
        scoring_service: RubricScoringService | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        try:
            self.extraction_service = extraction_service or get_document_extraction_service(
                db=db,
                settings=settings,
            )
            self.classification_service = classification_service or get_document_classification_service(
                db=db,
                settings=settings,
            )
            self.structured_extraction_service = (
                structured_extraction_service
                or get_structured_extraction_service(db=db, settings=settings)
            )
            self.summarization_service = summarization_service or get_summarization_service(
                db=db,
                settings=settings,
            )
            self.scoring_service = scoring_service or get_rubric_scoring_service(
                db=db,
                settings=settings,
            )
        except (
            AzureDocumentExtractionError,
            ClassificationServiceError,
            StructuredExtractionServiceError,
            SummarizationServiceError,
            RubricScoringServiceError,
        ) as exc:
            raise ApplicationProcessingConfigurationError(
                "Application processing services are not configured."
            ) from exc

    def process_application(
        self,
        application_id: str,
        *,
        force: bool = False,
        actor: AuthenticatedActor | None = None,
    ) -> ApplicationProcessingOutcome:
        self.actor = actor or system_actor()
        application = self.db.get(Application, application_id)
        if application is None:
            raise ApplicationProcessingApplicationNotFoundError("Application not found.")

        errors: list[dict[str, Any]] = []
        self._set_application_status(application, PROCESSING_STATUS_PENDING, errors)

        documents = self._documents_for_application(application_id)
        if not documents:
            errors.append(
                processing_error_record(
                    step="validation",
                    message="Application has no uploaded documents to process.",
                    blocking=True,
                )
            )
            self._set_application_status(application, PROCESSING_STATUS_FAILED, errors)
            raise ApplicationProcessingNoDocumentsError("Application has no uploaded documents.")

        extracted_document_ids = self._extract_documents(documents, force=force, errors=errors)
        self._classify_documents(documents, force=force, errors=errors, extracted_document_ids=extracted_document_ids)
        self._structure_and_summarize_documents(
            documents,
            force=force,
            errors=errors,
            extracted_document_ids=extracted_document_ids,
        )

        if not self._has_scoreable_evidence(application_id):
            errors.append(
                processing_error_record(
                    step=PROCESSING_STATUS_SCORING,
                    message="Scoring was blocked because no document produced usable OCR evidence.",
                    blocking=True,
                )
            )
            self._set_application_status(application, PROCESSING_STATUS_FAILED, errors)
            return ApplicationProcessingOutcome(
                application=application,
                documents=self._documents_for_application(application_id),
                score_rows=[],
                scorecard=None,
            )

        score_rows, scorecard = self._score_application(
            application_id,
            application=application,
            force=force,
            errors=errors,
        )

        final_status = self._final_status(
            errors=errors,
            documents=self._documents_for_application(application_id),
            scorecard=scorecard,
        )
        self._set_application_status(application, final_status, errors)
        application.review_status = final_status
        self.db.commit()
        self.db.refresh(application)

        logger.info("Completed processing pipeline for application %s with status %s", application_id, final_status)
        return ApplicationProcessingOutcome(
            application=application,
            documents=self._documents_for_application(application_id),
            score_rows=score_rows,
            scorecard=scorecard,
        )

    def status_for_application(self, application_id: str) -> ApplicationProcessingOutcome:
        application = self.db.get(Application, application_id)
        if application is None:
            raise ApplicationProcessingApplicationNotFoundError("Application not found.")
        return ApplicationProcessingOutcome(
            application=application,
            documents=self._documents_for_application(application_id),
            score_rows=self._score_rows_for_application(application_id),
            scorecard=self.db.get(RubricScorecard, application_id),
        )

    def _extract_documents(
        self,
        documents: list[ApplicationDocument],
        *,
        force: bool,
        errors: list[dict[str, Any]],
    ) -> set[str]:
        application = documents[0].application
        self._set_application_status(application, PROCESSING_STATUS_EXTRACTING, errors)
        extracted_document_ids: set[str] = set()
        for document in documents:
            try:
                self._ensure_extraction(document, force=force)
                extracted_document_ids.add(document.document_id)
            except (
                DocumentNotFoundError,
                UnsupportedDocumentTypeError,
                ExtractionTimeoutError,
                EmptyOcrResultError,
                AzureDocumentExtractionError,
            ) as exc:
                errors.append(
                    processing_error_record(
                        step=PROCESSING_STATUS_EXTRACTING,
                        message=safe_error_message(exc),
                        document_id=document.document_id,
                        error_type=exc.__class__.__name__,
                        blocking=False,
                    )
                )
                document.processing_status = PROCESSING_STATUS_FAILED
                application.processing_errors = list(errors)
                self.db.commit()
        return extracted_document_ids

    def _classify_documents(
        self,
        documents: list[ApplicationDocument],
        *,
        force: bool,
        errors: list[dict[str, Any]],
        extracted_document_ids: set[str],
    ) -> None:
        application = documents[0].application
        self._set_application_status(application, PROCESSING_STATUS_CLASSIFYING, errors)
        for document in documents:
            if document.document_id not in extracted_document_ids:
                continue
            try:
                self._ensure_classification(document, force=force)
            except (ClassificationDocumentNotFoundError, ClassificationServiceError) as exc:
                errors.append(
                    processing_error_record(
                        step=PROCESSING_STATUS_CLASSIFYING,
                        message=safe_error_message(exc),
                        document_id=document.document_id,
                        error_type=exc.__class__.__name__,
                        blocking=False,
                    )
                )
                document.processing_status = PROCESSING_STATUS_FAILED
                application.processing_errors = list(errors)
                self.db.commit()

    def _structure_and_summarize_documents(
        self,
        documents: list[ApplicationDocument],
        *,
        force: bool,
        errors: list[dict[str, Any]],
        extracted_document_ids: set[str],
    ) -> None:
        application = documents[0].application
        self._set_application_status(application, PROCESSING_STATUS_SUMMARIZING, errors)
        for document in documents:
            if document.document_id not in extracted_document_ids:
                continue

            try:
                self._ensure_structured_extraction(document, force=force)
            except (
                StructuredExtractionDocumentNotFoundError,
                StructuredExtractionRequiresOcrError,
                UnsupportedStructuredExtractionTypeError,
                StructuredExtractionServiceError,
            ) as exc:
                errors.append(
                    processing_error_record(
                        step="structured_extracting",
                        message=safe_error_message(exc),
                        document_id=document.document_id,
                        error_type=exc.__class__.__name__,
                        blocking=False,
                    )
                )
                application.processing_errors = list(errors)
                self.db.commit()

            try:
                self._ensure_summary(document, force=force)
            except (
                SummarizationDocumentNotFoundError,
                SummarizationRequiresOcrError,
                SummarizationServiceError,
            ) as exc:
                errors.append(
                    processing_error_record(
                        step=PROCESSING_STATUS_SUMMARIZING,
                        message=safe_error_message(exc),
                        document_id=document.document_id,
                        error_type=exc.__class__.__name__,
                        blocking=False,
                    )
                )
                document.processing_status = PROCESSING_STATUS_FAILED
                application.processing_errors = list(errors)
                self.db.commit()

    def _score_application(
        self,
        application_id: str,
        *,
        application: Application,
        force: bool,
        errors: list[dict[str, Any]],
    ) -> tuple[list[RubricCriterionScore], RubricScorecard | None]:
        self._set_application_status(application, PROCESSING_STATUS_SCORING, errors)
        existing_scorecard = self.db.get(RubricScorecard, application_id)
        existing_scores = self._score_rows_for_application(application_id)
        if existing_scorecard is not None and existing_scores and not force:
            return existing_scores, existing_scorecard

        old_value = (
            {
                "weighted_score": existing_scorecard.weighted_score,
                "recommendation_band": existing_scorecard.recommendation_band,
                "requires_human_review": existing_scorecard.requires_human_review,
            }
            if existing_scorecard is not None
            else None
        )

        try:
            outcome = self.scoring_service.score_application(application_id)
            score_rows, scorecard = replace_persisted_scores(
                db=self.db,
                application_id=application_id,
                outcome=outcome,
                rubric=outcome.rubric,
            )
            record_audit_log(
                db=self.db,
                actor=getattr(self, "actor", system_actor()),
                action=AuditAction.APPLICATION_SCORING,
                application_id=application_id,
                old_value=old_value,
                new_value={
                    "weighted_score": scorecard.weighted_score,
                    "max_score": scorecard.max_score,
                    "recommendation_band": scorecard.recommendation_band,
                    "requires_human_review": scorecard.requires_human_review,
                    "criterion_count": len(score_rows),
                },
            )
            self.db.commit()
            return score_rows, scorecard
        except (RubricScoringApplicationNotFoundError, RubricScoringServiceError) as exc:
            errors.append(
                processing_error_record(
                    step=PROCESSING_STATUS_SCORING,
                    message=safe_error_message(exc),
                    error_type=exc.__class__.__name__,
                    blocking=True,
                )
            )
            application.processing_errors = list(errors)
            self.db.commit()
            return [], None

    def _ensure_extraction(self, document: ApplicationDocument, *, force: bool) -> ExtractedDocumentContent:
        existing = self.db.get(ExtractedDocumentContent, document.document_id)
        if existing is not None and not force:
            return existing
        old_value = extraction_audit_value(document=document, extracted=existing)

        extracted = self.extraction_service.extract_document(document.document_id)
        if existing is not None:
            existing.raw_text = extracted.raw_text
            existing.pages = extracted.pages
            existing.tables = extracted.tables
            existing.key_value_pairs = extracted.key_value_pairs
            existing.extraction_confidence = extracted.extraction_confidence
            existing.extraction_metadata = extracted.extraction_metadata
            existing.structured_extraction = {} if force else existing.structured_extraction
            persisted = existing
        else:
            self.db.add(extracted)
            persisted = extracted

        document.page_count = resolved_document_page_count(persisted)
        document.processing_status = "extracted"
        record_audit_log(
            db=self.db,
            actor=getattr(self, "actor", system_actor()),
            action=AuditAction.DOCUMENT_EXTRACTION,
            application_id=document.application_id,
            document_id=document.document_id,
            old_value=old_value,
            new_value=extraction_audit_value(document=document, extracted=persisted),
        )
        self.db.commit()
        self.db.refresh(persisted)
        return persisted

    def _ensure_classification(self, document: ApplicationDocument, *, force: bool) -> None:
        if document.document_type and document.classification_metadata and not force:
            return

        old_value = classification_audit_value(document)
        result = self.classification_service.classify_document(document.document_id)
        existing_metadata = document.classification_metadata or {}
        document.document_type = result.document_type
        document.classification_confidence = result.confidence
        document.classification_metadata = {
            **preserved_classification_metadata(existing_metadata),
            **classification_metadata(result),
        }
        document.processing_status = "classified"
        record_audit_log(
            db=self.db,
            actor=getattr(self, "actor", system_actor()),
            action=AuditAction.DOCUMENT_CLASSIFICATION,
            application_id=document.application_id,
            document_id=document.document_id,
            old_value=old_value,
            new_value=classification_audit_value(document),
        )
        self.db.commit()
        self.db.refresh(document)

    def _ensure_structured_extraction(self, document: ApplicationDocument, *, force: bool) -> None:
        extracted_content = self.db.get(ExtractedDocumentContent, document.document_id)
        if extracted_content is None:
            raise StructuredExtractionRequiresOcrError("OCR extraction is required before structured extraction.")
        if extracted_content.structured_extraction and not force:
            return

        old_value = {
            "document_type": document.document_type,
            "had_structured_extraction": bool(extracted_content.structured_extraction),
            "processing_status": document.processing_status,
        }
        result = self.structured_extraction_service.structured_extract(document.document_id)
        extraction_payload = result.extraction.model_dump(mode="json")
        metadata = {
            **result.metadata,
            "requires_human_review": structured_extraction_requires_human_review(extraction_payload),
        }
        extracted_content.structured_extraction = {
            "document_type": result.document_type,
            "extraction": extraction_payload,
            "metadata": metadata,
        }
        document.processing_status = "structured_extracted"
        record_audit_log(
            db=self.db,
            actor=getattr(self, "actor", system_actor()),
            action=AuditAction.DOCUMENT_STRUCTURED_EXTRACTION,
            application_id=document.application_id,
            document_id=document.document_id,
            old_value=old_value,
            new_value={
                "document_type": result.document_type,
                "requires_human_review": metadata["requires_human_review"],
                "processing_status": document.processing_status,
                "prompt_name": metadata.get("prompt_name"),
                "prompt_version": metadata.get("prompt_version"),
            },
        )
        self.db.commit()
        self.db.refresh(extracted_content)

    def _ensure_summary(self, document: ApplicationDocument, *, force: bool) -> DocumentSummary:
        existing = self.db.get(DocumentSummary, document.document_id)
        if existing is not None and not force:
            return existing
        old_value = summary_audit_value(document=document, summary=existing)

        result = self.summarization_service.summarize_document(document.document_id)
        summary = summary_result_to_model(document_id=document.document_id, result=result)
        if existing is not None:
            existing.short_summary = summary.short_summary
            existing.section_summaries = summary.section_summaries
            existing.strengths = summary.strengths
            existing.concerns = summary.concerns
            existing.missing_information = summary.missing_information
            existing.reviewer_attention_points = summary.reviewer_attention_points
            existing.evidence = summary.evidence
            existing.summary_metadata = summary.summary_metadata
            persisted = existing
        else:
            self.db.add(summary)
            persisted = summary

        document.processing_status = "summarized"
        record_audit_log(
            db=self.db,
            actor=getattr(self, "actor", system_actor()),
            action=AuditAction.DOCUMENT_SUMMARIZATION,
            application_id=document.application_id,
            document_id=document.document_id,
            old_value=old_value,
            new_value=summary_audit_value(document=document, summary=persisted),
        )
        self.db.commit()
        self.db.refresh(persisted)
        return persisted

    def _has_scoreable_evidence(self, application_id: str) -> bool:
        documents = self._documents_for_application(application_id)
        for document in documents:
            extracted = self.db.get(ExtractedDocumentContent, document.document_id)
            if extracted is None:
                continue
            if (extracted.raw_text or "").strip() or extracted.pages:
                return True
        return False

    def _final_status(
        self,
        *,
        errors: list[dict[str, Any]],
        documents: list[ApplicationDocument],
        scorecard: RubricScorecard | None,
    ) -> str:
        if any(error.get("blocking") for error in errors) or scorecard is None:
            return PROCESSING_STATUS_FAILED
        if errors or scorecard.requires_human_review:
            return PROCESSING_STATUS_NEEDS_MANUAL_REVIEW
        if any(document_requires_human_review(document) for document in documents):
            return PROCESSING_STATUS_NEEDS_MANUAL_REVIEW
        return PROCESSING_STATUS_READY_FOR_REVIEW

    def _set_application_status(
        self,
        application: Application,
        status_value: str,
        errors: list[dict[str, Any]],
    ) -> None:
        application.processing_status = status_value
        application.processing_errors = list(errors)
        self.db.commit()
        self.db.refresh(application)

    def _documents_for_application(self, application_id: str) -> list[ApplicationDocument]:
        return list(
            self.db.scalars(
                select(ApplicationDocument)
                .where(ApplicationDocument.application_id == application_id)
                .order_by(ApplicationDocument.created_at)
            )
        )

    def _score_rows_for_application(self, application_id: str) -> list[RubricCriterionScore]:
        return list(
            self.db.scalars(
                select(RubricCriterionScore)
                .where(RubricCriterionScore.application_id == application_id)
                .order_by(RubricCriterionScore.criterion_id)
            )
        )


def processing_error_record(
    *,
    step: str,
    message: str,
    document_id: str | None = None,
    error_type: str | None = None,
    blocking: bool,
) -> dict[str, Any]:
    return {
        "step": step,
        "document_id": document_id,
        "error_type": error_type or "ApplicationProcessingError",
        "message": message,
        "blocking": blocking,
    }


def safe_error_message(exc: Exception) -> str:
    message = str(exc).strip()
    return message or exc.__class__.__name__


def classification_metadata(result: ClassificationResult) -> dict[str, Any]:
    return {
        **document_classification_prompt_metadata(),
        "rationale": result.rationale,
        "evidence_snippets": result.evidence_snippets,
        "requires_human_review": result.requires_human_review,
        "source": result.source,
    }


def preserved_classification_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    preserved_keys = (
        "file_size",
        "file_size_bytes",
        "content_type",
        "intake_upload",
        "rubric_id",
        "rubric_name",
        "student_unique_id",
        "program_applied",
        "intake_term",
    )
    return {
        key: metadata[key]
        for key in preserved_keys
        if key in metadata
    }


def extraction_audit_value(
    *,
    document: ApplicationDocument,
    extracted: ExtractedDocumentContent | None,
) -> dict[str, Any]:
    return {
        "processing_status": document.processing_status,
        "page_count": document.page_count,
        "extraction_confidence": extracted.extraction_confidence if extracted else None,
        "provider": (extracted.extraction_metadata or {}).get("provider") if extracted else None,
        "model_id": (extracted.extraction_metadata or {}).get("model_id") if extracted else None,
    }


def classification_audit_value(document: ApplicationDocument) -> dict[str, Any]:
    metadata = document.classification_metadata or {}
    return {
        "document_type": document.document_type,
        "classification_confidence": document.classification_confidence,
        "requires_human_review": metadata.get("requires_human_review"),
        "source": metadata.get("source"),
        "prompt_name": metadata.get("prompt_name"),
        "prompt_version": metadata.get("prompt_version"),
    }


def summary_audit_value(
    *,
    document: ApplicationDocument,
    summary: DocumentSummary | None,
) -> dict[str, Any]:
    metadata = summary.summary_metadata or {} if summary else {}
    return {
        "processing_status": document.processing_status,
        "has_summary": summary is not None,
        "section_summary_count": len(summary.section_summaries or []) if summary else 0,
        "strength_count": len(summary.strengths or []) if summary else 0,
        "concern_count": len(summary.concerns or []) if summary else 0,
        "missing_information_count": len(summary.missing_information or []) if summary else 0,
        "prompt_name": metadata.get("prompt_name"),
        "prompt_version": metadata.get("prompt_version"),
    }


def structured_extraction_requires_human_review(value: object) -> bool:
    if isinstance(value, dict):
        if value.get("uncertain") is True:
            return True
        return any(structured_extraction_requires_human_review(item) for item in value.values())
    if isinstance(value, list):
        return any(structured_extraction_requires_human_review(item) for item in value)
    return False




def document_requires_human_review(document: ApplicationDocument) -> bool:
    metadata = document.classification_metadata or {}
    if metadata.get("requires_human_review") is True:
        return True
    structured_metadata = (document.extracted_content.structured_extraction or {}).get("metadata", {}) if document.extracted_content else {}
    if structured_metadata.get("requires_human_review") is True:
        return True
    if document.summary:
        return bool(document.summary.concerns or document.summary.missing_information)
    return False
