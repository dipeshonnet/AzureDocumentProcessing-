from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.config import AppSettings, get_settings
from app.db.dependencies import get_db
from app.models import ApplicationDocument, DocumentSummary, ExtractedDocumentContent
from app.security import AuthenticatedActor, UserRole, require_roles
from app.schemas.admissions import (
    DocumentClassificationRead,
    DocumentSummaryRead,
    ExtractedDocumentContentRead,
    StructuredExtractionRead,
)
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
from app.services.audit import AuditAction, record_audit_log


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])


def document_extraction_service_dependency(
    db: Session = Depends(get_db),
    settings: AppSettings = Depends(get_settings),
) -> DocumentExtractionService:
    try:
        return get_document_extraction_service(db=db, settings=settings)
    except AzureDocumentExtractionError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Document extraction service is not configured.",
        ) from exc


def document_classification_service_dependency(
    db: Session = Depends(get_db),
    settings: AppSettings = Depends(get_settings),
) -> TwoStageClassificationService:
    try:
        return get_document_classification_service(db=db, settings=settings)
    except ClassificationServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Document classification service is not configured.",
        ) from exc


def structured_extraction_service_dependency(
    db: Session = Depends(get_db),
    settings: AppSettings = Depends(get_settings),
) -> StructuredExtractionService:
    try:
        return get_structured_extraction_service(db=db, settings=settings)
    except StructuredExtractionServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Structured extraction service is not configured.",
        ) from exc


def summarization_service_dependency(
    db: Session = Depends(get_db),
    settings: AppSettings = Depends(get_settings),
) -> SummarizationService:
    try:
        return get_summarization_service(db=db, settings=settings)
    except SummarizationServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Document summarization service is not configured.",
        ) from exc


@router.post("/{document_id}/extract", response_model=ExtractedDocumentContentRead)
def extract_document(
    document_id: str,
    force: bool = Query(default=False),
    db: Session = Depends(get_db),
    extraction_service: DocumentExtractionService = Depends(document_extraction_service_dependency),
    actor: AuthenticatedActor = Depends(require_roles(*(
        UserRole.ADMIN,
        UserRole.ADMISSIONS_REVIEWER,
    ))),
) -> ExtractedDocumentContent:
    document = db.get(ApplicationDocument, document_id)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        )

    existing = db.get(ExtractedDocumentContent, document_id)
    if existing is not None and not force:
        return existing
    old_value = _extraction_audit_value(document=document, extracted=existing)

    try:
        extracted = extraction_service.extract_document(document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        ) from exc
    except UnsupportedDocumentTypeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except ExtractionTimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Document extraction timed out.",
        ) from exc
    except EmptyOcrResultError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Document extraction returned no OCR content.",
        ) from exc
    except AzureDocumentExtractionError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Azure Document Intelligence extraction failed.",
        ) from exc

    if not (extracted.raw_text or "").strip() and not extracted.pages:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Document extraction returned no OCR content.",
        )

    if existing is not None:
        existing.raw_text = extracted.raw_text
        existing.pages = extracted.pages
        existing.tables = extracted.tables
        existing.key_value_pairs = extracted.key_value_pairs
        existing.extraction_confidence = extracted.extraction_confidence
        existing.extraction_metadata = extracted.extraction_metadata
        persisted = existing
    else:
        db.add(extracted)
        persisted = extracted

    document.page_count = len(persisted.pages)
    document.processing_status = "extracted"
    record_audit_log(
        db=db,
        actor=actor,
        action=AuditAction.DOCUMENT_EXTRACTION,
        application_id=document.application_id,
        document_id=document_id,
        old_value=old_value,
        new_value=_extraction_audit_value(document=document, extracted=persisted),
    )
    db.commit()
    db.refresh(persisted)
    logger.info("Persisted extraction metadata for document %s", document_id)
    return persisted


@router.post("/{document_id}/classify", response_model=DocumentClassificationRead)
def classify_document(
    document_id: str,
    db: Session = Depends(get_db),
    classification_service: TwoStageClassificationService = Depends(
        document_classification_service_dependency
    ),
    actor: AuthenticatedActor = Depends(require_roles(*(
        UserRole.ADMIN,
        UserRole.ADMISSIONS_REVIEWER,
    ))),
) -> DocumentClassificationRead:
    document = db.get(ApplicationDocument, document_id)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        )

    try:
        result = classification_service.classify_document(document_id)
    except ClassificationDocumentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        ) from exc
    except ClassificationServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Document classification failed.",
        ) from exc

    old_value = _classification_audit_value(document)
    document.document_type = result.document_type
    document.classification_confidence = result.confidence
    document.classification_metadata = _classification_metadata(result)
    record_audit_log(
        db=db,
        actor=actor,
        action=AuditAction.DOCUMENT_CLASSIFICATION,
        application_id=document.application_id,
        document_id=document_id,
        old_value=old_value,
        new_value=_classification_audit_value(document),
    )
    db.commit()
    db.refresh(document)

    logger.info("Persisted classification metadata for document %s", document_id)
    return DocumentClassificationRead(
        document_id=document_id,
        document_type=result.document_type,
        confidence=result.confidence,
        rationale=result.rationale,
        evidence_snippets=result.evidence_snippets,
        requires_human_review=result.requires_human_review,
        source=result.source,
        metadata=document.classification_metadata,
    )


def _classification_metadata(result: ClassificationResult) -> dict:
    return {
        **document_classification_prompt_metadata(),
        "rationale": result.rationale,
        "evidence_snippets": result.evidence_snippets,
        "requires_human_review": result.requires_human_review,
        "source": result.source,
    }


@router.post("/{document_id}/structured-extract", response_model=StructuredExtractionRead)
def structured_extract_document(
    document_id: str,
    db: Session = Depends(get_db),
    structured_extraction_service: StructuredExtractionService = Depends(
        structured_extraction_service_dependency
    ),
    actor: AuthenticatedActor = Depends(require_roles(*(
        UserRole.ADMIN,
        UserRole.ADMISSIONS_REVIEWER,
    ))),
) -> StructuredExtractionRead:
    document = db.get(ApplicationDocument, document_id)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        )

    try:
        result = structured_extraction_service.structured_extract(document_id)
    except StructuredExtractionDocumentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        ) from exc
    except StructuredExtractionRequiresOcrError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OCR extraction is required before structured extraction.",
        ) from exc
    except UnsupportedStructuredExtractionTypeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except StructuredExtractionServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Structured extraction failed.",
        ) from exc

    extracted_content = db.get(ExtractedDocumentContent, document_id)
    if extracted_content is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OCR extraction is required before structured extraction.",
        )

    old_value = {
        "document_type": document.document_type,
        "had_structured_extraction": bool(extracted_content.structured_extraction),
        "processing_status": document.processing_status,
    }
    extraction_payload = result.extraction.model_dump(mode="json")
    metadata = {
        **result.metadata,
        "requires_human_review": _structured_extraction_requires_human_review(extraction_payload),
    }
    extracted_content.structured_extraction = {
        "document_type": result.document_type,
        "extraction": extraction_payload,
        "metadata": metadata,
    }
    document.processing_status = "structured_extracted"
    record_audit_log(
        db=db,
        actor=actor,
        action=AuditAction.DOCUMENT_STRUCTURED_EXTRACTION,
        application_id=document.application_id,
        document_id=document_id,
        old_value=old_value,
        new_value={
            "document_type": result.document_type,
            "requires_human_review": metadata["requires_human_review"],
            "processing_status": document.processing_status,
            "prompt_name": metadata.get("prompt_name"),
            "prompt_version": metadata.get("prompt_version"),
        },
    )
    db.commit()
    db.refresh(extracted_content)

    logger.info("Persisted structured extraction metadata for document %s", document_id)
    return StructuredExtractionRead(
        document_id=document_id,
        document_type=result.document_type,
        extraction=extraction_payload,
        metadata=metadata,
    )


def _structured_extraction_requires_human_review(value: object) -> bool:
    if isinstance(value, dict):
        if value.get("uncertain") is True:
            return True
        return any(_structured_extraction_requires_human_review(item) for item in value.values())
    if isinstance(value, list):
        return any(_structured_extraction_requires_human_review(item) for item in value)
    return False


@router.post("/{document_id}/summarize", response_model=DocumentSummaryRead)
def summarize_document(
    document_id: str,
    force: bool = Query(default=False),
    db: Session = Depends(get_db),
    summarization_service: SummarizationService = Depends(summarization_service_dependency),
    actor: AuthenticatedActor = Depends(require_roles(*(
        UserRole.ADMIN,
        UserRole.ADMISSIONS_REVIEWER,
    ))),
) -> DocumentSummary:
    document = db.get(ApplicationDocument, document_id)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        )

    existing = db.get(DocumentSummary, document_id)
    if existing is not None and not force:
        return existing
    old_value = _summary_audit_value(document=document, summary=existing)

    try:
        result = summarization_service.summarize_document(document_id)
    except SummarizationDocumentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        ) from exc
    except SummarizationRequiresOcrError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OCR extraction is required before summarization.",
        ) from exc
    except SummarizationServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Document summarization failed.",
        ) from exc

    summary = summary_result_to_model(document_id=document_id, result=result)
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
        db.add(summary)
        persisted = summary

    document.processing_status = "summarized"
    record_audit_log(
        db=db,
        actor=actor,
        action=AuditAction.DOCUMENT_SUMMARIZATION,
        application_id=document.application_id,
        document_id=document_id,
        old_value=old_value,
        new_value=_summary_audit_value(document=document, summary=persisted),
    )
    db.commit()
    db.refresh(persisted)

    logger.info("Persisted summary metadata for document %s", document_id)
    return persisted


def _extraction_audit_value(
    *,
    document: ApplicationDocument,
    extracted: ExtractedDocumentContent | None,
) -> dict:
    return {
        "processing_status": document.processing_status,
        "page_count": document.page_count,
        "extraction_confidence": extracted.extraction_confidence if extracted else None,
        "provider": (extracted.extraction_metadata or {}).get("provider") if extracted else None,
        "model_id": (extracted.extraction_metadata or {}).get("model_id") if extracted else None,
    }


def _classification_audit_value(document: ApplicationDocument) -> dict:
    return {
        "document_type": document.document_type,
        "classification_confidence": document.classification_confidence,
        "requires_human_review": (document.classification_metadata or {}).get("requires_human_review"),
        "source": (document.classification_metadata or {}).get("source"),
        "prompt_name": (document.classification_metadata or {}).get("prompt_name"),
        "prompt_version": (document.classification_metadata or {}).get("prompt_version"),
    }


def _summary_audit_value(
    *,
    document: ApplicationDocument,
    summary: DocumentSummary | None,
) -> dict:
    return {
        "processing_status": document.processing_status,
        "has_summary": summary is not None,
        "section_summary_count": len(summary.section_summaries or []) if summary else 0,
        "strength_count": len(summary.strengths or []) if summary else 0,
        "concern_count": len(summary.concerns or []) if summary else 0,
        "missing_information_count": len(summary.missing_information or []) if summary else 0,
        "prompt_name": (summary.summary_metadata or {}).get("prompt_name") if summary else None,
        "prompt_version": (summary.summary_metadata or {}).get("prompt_version") if summary else None,
    }
