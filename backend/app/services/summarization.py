from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy.orm import Session

from app.config import AppSettings
from app.models import ApplicationDocument, DocumentSummary, ExtractedDocumentContent
from app.services.azure_openai import chat_completion_kwargs, create_azure_openai_chat_client
from app.services.classification import detect_headings
from app.services.prompts import load_prompt


logger = logging.getLogger(__name__)


class SummarizationError(Exception):
    """Base error for document summarization failures."""


class SummarizationDocumentNotFoundError(SummarizationError):
    pass


class SummarizationRequiresOcrError(SummarizationError):
    pass


class SummarizationServiceError(SummarizationError):
    pass


class SummaryEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snippet: str = Field(min_length=1)
    page_number: int | None = Field(..., ge=1)
    source: str | None = Field(...)


class SectionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    evidence: list[SummaryEvidence] = Field(min_length=1)


class DocumentSummaryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    short_summary: str = Field(min_length=1)
    section_summaries: list[SectionSummary]
    strengths: list[str]
    concerns: list[str]
    missing_information: list[str]
    reviewer_attention_points: list[str]
    evidence: list[SummaryEvidence] = Field(min_length=1)

    @model_validator(mode="after")
    def require_grounding(self) -> DocumentSummaryResult:
        for section in self.section_summaries:
            if not section.evidence:
                raise ValueError("section summaries require evidence")
        return self


@dataclass(frozen=True)
class SummarizationPayload:
    filename: str
    document_type: str | None
    text: str
    detected_headings: list[str]
    extraction_confidence: float | None
    page_count: int


class SummarizationService(Protocol):
    def summarize_document(self, document_id: str) -> DocumentSummaryResult:
        raise NotImplementedError


class BaseSummarizationService:
    def __init__(self, *, db: Session, settings: AppSettings) -> None:
        self.db = db
        self.settings = settings

    def summarize_document(self, document_id: str) -> DocumentSummaryResult:
        document = self.db.get(ApplicationDocument, document_id)
        if document is None:
            raise SummarizationDocumentNotFoundError("Document not found.")

        extracted_content = self.db.get(ExtractedDocumentContent, document_id)
        if extracted_content is None:
            raise SummarizationRequiresOcrError("OCR extraction is required before summarization.")

        payload = build_summarization_payload(
            document=document,
            extracted_content=extracted_content,
            max_chars=self.settings.document_summary_text_max_chars,
        )
        if is_unreadable_or_low_confidence(
            payload,
            threshold=self.settings.document_summary_low_confidence_threshold,
        ):
            return unreadable_summary(payload)

        return self.generate_summary(payload)

    def generate_summary(self, payload: SummarizationPayload) -> DocumentSummaryResult:
        raise NotImplementedError


class MockSummarizationService(BaseSummarizationService):
    def __init__(
        self,
        *,
        db: Session,
        settings: AppSettings,
        result: DocumentSummaryResult | None = None,
    ) -> None:
        super().__init__(db=db, settings=settings)
        self.result = result

    def generate_summary(self, payload: SummarizationPayload) -> DocumentSummaryResult:
        if self.result is not None:
            return self.result

        evidence = [
            SummaryEvidence(
                snippet=(payload.text[:120] or "Mock extracted evidence."),
                page_number=1 if payload.page_count else None,
                source="mock",
            )
        ]
        return DocumentSummaryResult(
            short_summary="Mock summary grounded in extracted document text.",
            section_summaries=[
                SectionSummary(
                    section_title="Document overview",
                    summary="The document contains review-relevant admissions information.",
                    evidence=evidence,
                )
            ],
            strengths=["Clear evidence is available for reviewer inspection."],
            concerns=[],
            missing_information=[],
            reviewer_attention_points=["Verify extracted evidence against the source document."],
            evidence=evidence,
        )


class AzureOpenAISummarizationService(BaseSummarizationService):
    def __init__(self, *, db: Session, settings: AppSettings) -> None:
        super().__init__(db=db, settings=settings)
        if not settings.azure_openai_endpoint:
            raise SummarizationServiceError("Azure OpenAI endpoint is not configured.")
        if not settings.azure_openai_api_key:
            raise SummarizationServiceError("Azure OpenAI API key is not configured.")
        if not settings.azure_openai_api_version:
            raise SummarizationServiceError("Azure OpenAI API version is not configured.")
        deployment = settings.azure_openai_summarization_model_deployment
        if not deployment:
            raise SummarizationServiceError("Azure OpenAI summarization deployment is not configured.")

        self.settings = settings
        self.client = create_azure_openai_chat_client(settings)
        self.deployment = deployment

    def generate_summary(self, payload: SummarizationPayload) -> DocumentSummaryResult:
        try:
            response = self.client.chat.completions.create(
                **chat_completion_kwargs(
                    model=self.deployment,
                    messages=[
                        {
                            "role": "user",
                            "content": build_summarization_prompt(payload),
                        },
                    ],
                    response_format=summarization_response_format(),
                    settings=self.settings,
                )
            )
            content = response.choices[0].message.content
            return DocumentSummaryResult.model_validate_json(content or "{}")
        except (ValidationError, IndexError, AttributeError) as exc:
            raise SummarizationServiceError("Azure OpenAI returned invalid summary JSON.") from exc
        except Exception as exc:
            raise SummarizationServiceError("Azure OpenAI summarization failed.") from exc


def get_summarization_service(
    *,
    db: Session,
    settings: AppSettings,
) -> SummarizationService:
    backend = settings.document_summarization_backend.strip().lower()
    if backend == "mock":
        return MockSummarizationService(db=db, settings=settings)
    if backend == "azure":
        return AzureOpenAISummarizationService(db=db, settings=settings)
    raise SummarizationServiceError(f"Unsupported summarization backend: {backend}.")


def build_summarization_payload(
    *,
    document: ApplicationDocument,
    extracted_content: ExtractedDocumentContent,
    max_chars: int,
) -> SummarizationPayload:
    text_parts = []
    for page in (extracted_content.pages or [])[:3]:
        page_text = str(page.get("text") or "").strip()
        if page_text:
            text_parts.append(f"Page {page.get('page_number') or '?'}:\n{page_text}")

    text = "\n\n".join(text_parts).strip()
    if not text:
        text = (extracted_content.raw_text or "").strip()

    return SummarizationPayload(
        filename=document.original_filename,
        document_type=document.document_type,
        text=text[:max_chars],
        detected_headings=detect_headings(extracted_content.pages or [], max_headings=16),
        extraction_confidence=extracted_content.extraction_confidence,
        page_count=len(extracted_content.pages or []),
    )


def is_unreadable_or_low_confidence(payload: SummarizationPayload, *, threshold: float) -> bool:
    if not payload.text.strip():
        return True
    return payload.extraction_confidence is not None and payload.extraction_confidence < threshold


def unreadable_summary(payload: SummarizationPayload) -> DocumentSummaryResult:
    reason = "No readable OCR text was available."
    if payload.text.strip() and payload.extraction_confidence is not None:
        reason = f"OCR confidence is low ({payload.extraction_confidence:.2f})."

    evidence = [
        SummaryEvidence(
            snippet=reason,
            page_number=1 if payload.page_count else None,
            source="ocr_quality",
        )
    ]
    return DocumentSummaryResult(
        short_summary=f"This document may be unreadable or low-confidence. {reason}",
        section_summaries=[
            SectionSummary(
                section_title="OCR quality",
                summary=reason,
                evidence=evidence,
            )
        ],
        strengths=[],
        concerns=["Document content could not be summarized reliably from OCR."],
        missing_information=["Readable document text is required before reliable summarization."],
        reviewer_attention_points=[
            "Review the original document manually.",
            "Consider re-uploading a clearer copy if available.",
        ],
        evidence=evidence,
    )


def summarization_response_format() -> dict:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "DocumentSummaryResult",
            "strict": True,
            "schema": DocumentSummaryResult.model_json_schema(),
        },
    }


def build_summarization_prompt(payload: SummarizationPayload) -> str:
    confidence = (
        f"{payload.extraction_confidence:.2f}"
        if payload.extraction_confidence is not None
        else "unknown"
    )
    template = load_prompt("document_summary")
    return template.render(
        {
            "filename": payload.filename,
            "document_type": payload.document_type or "unclassified",
            "ocr_confidence": confidence,
            "detected_headings": payload.detected_headings,
            "ocr_excerpt": payload.text,
        }
    )


def summary_result_to_model(*, document_id: str, result: DocumentSummaryResult) -> DocumentSummary:
    return DocumentSummary(
        document_id=document_id,
        short_summary=result.short_summary,
        section_summaries=[item.model_dump(mode="json") for item in result.section_summaries],
        strengths=result.strengths,
        concerns=result.concerns,
        missing_information=result.missing_information,
        reviewer_attention_points=result.reviewer_attention_points,
        evidence=[item.model_dump(mode="json") for item in result.evidence],
        summary_metadata=load_prompt("document_summary").identity(),
    )
