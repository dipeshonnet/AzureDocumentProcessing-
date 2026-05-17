from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from app.config import AppSettings
from app.models import ApplicationDocument, ExtractedDocumentContent
from app.services.azure_openai import chat_completion_kwargs, create_azure_openai_chat_client
from app.services.prompts import load_prompt, prompt_identity


logger = logging.getLogger(__name__)

SUPPORTED_DOCUMENT_TYPES: tuple[str, ...] = (
    "personal_statement",
    "transcript",
    "recommendation_letter",
    "resume_cv",
    "test_score_report",
    "application_form",
    "other_or_unreadable",
)

LOW_CONFIDENCE_TYPE = "other_or_unreadable"


class ClassificationError(Exception):
    """Base error for document classification failures."""


class ClassificationDocumentNotFoundError(ClassificationError):
    pass


class ClassificationServiceError(ClassificationError):
    pass


@dataclass(frozen=True)
class ClassificationPayload:
    filename: str
    text: str = ""
    detected_headings: list[str] = field(default_factory=list)


class ClassificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_type: str = Field(pattern=f"^({'|'.join(SUPPORTED_DOCUMENT_TYPES)})$")
    confidence: float = Field(ge=0, le=1)
    rationale: str
    evidence_snippets: list[str] = Field(default_factory=list)
    requires_human_review: bool
    source: str = "unknown"


class LLMClassificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_type: str = Field(pattern=f"^({'|'.join(SUPPORTED_DOCUMENT_TYPES)})$")
    confidence: float = Field(ge=0, le=1)
    rationale: str
    evidence_snippets: list[str]
    requires_human_review: bool


class ClassificationService(Protocol):
    def classify(self, payload: ClassificationPayload) -> ClassificationResult:
        raise NotImplementedError


class RuleBasedClassificationService:
    def classify(self, payload: ClassificationPayload) -> ClassificationResult:
        filename = payload.filename.lower()
        normalized_text = _normalize_text(
            "\n".join([payload.text, *payload.detected_headings])
        )

        filename_result = self._classify_filename(filename)
        if filename_result:
            return filename_result

        text_result = self._classify_text(normalized_text)
        if text_result:
            return text_result

        return ClassificationResult(
            document_type=LOW_CONFIDENCE_TYPE,
            confidence=0.2,
            rationale="No high-confidence rule matched the filename, headings, or OCR text.",
            evidence_snippets=[],
            requires_human_review=True,
            source="rule_based",
        )

    @staticmethod
    def _classify_filename(filename: str) -> ClassificationResult | None:
        rules: tuple[tuple[str, tuple[str, ...], float, str], ...] = (
            (
                "personal_statement",
                ("personal_statement", "statement_of_purpose", "sop", "admission_essay"),
                0.96,
                "Filename indicates a personal statement or statement of purpose.",
            ),
            (
                "transcript",
                ("transcript", "marksheet", "mark_sheet", "grade_report", "academic_record"),
                0.96,
                "Filename indicates an academic transcript or grade report.",
            ),
            (
                "recommendation_letter",
                ("recommendation", "reference_letter", "ref_letter", "lor"),
                0.95,
                "Filename indicates a recommendation or reference letter.",
            ),
            (
                "resume_cv",
                ("resume", "cv", "curriculum_vitae"),
                0.95,
                "Filename indicates a resume or curriculum vitae.",
            ),
            (
                "test_score_report",
                ("toefl", "ielts", "gre", "gmat", "sat", "act", "score_report", "test_score"),
                0.95,
                "Filename indicates a standardized test score report.",
            ),
            (
                "application_form",
                ("application_form", "admission_form", "application_packet"),
                0.93,
                "Filename indicates an application form.",
            ),
        )
        for document_type, tokens, confidence, rationale in rules:
            matched = [token for token in tokens if token in filename]
            if matched:
                return ClassificationResult(
                    document_type=document_type,
                    confidence=confidence,
                    rationale=rationale,
                    evidence_snippets=matched[:3],
                    requires_human_review=False,
                    source="rule_based",
                )
        return None

    @staticmethod
    def _classify_text(text: str) -> ClassificationResult | None:
        rules: tuple[tuple[str, tuple[str, ...], float, str], ...] = (
            (
                "personal_statement",
                ("personal statement", "statement of purpose", "why i am applying", "my academic journey"),
                0.9,
                "OCR text contains personal statement language.",
            ),
            (
                "transcript",
                ("official transcript", "grade point average", "semester credits", "course grade"),
                0.9,
                "OCR text contains transcript language.",
            ),
            (
                "recommendation_letter",
                ("letter of recommendation", "i recommend", "pleased to recommend", "recommender"),
                0.9,
                "OCR text contains recommendation letter language.",
            ),
            (
                "resume_cv",
                ("curriculum vitae", "work experience", "professional experience", "skills"),
                0.88,
                "OCR text contains resume or CV language.",
            ),
            (
                "test_score_report",
                ("toefl", "ielts", "gre", "gmat", "sat score", "act score"),
                0.9,
                "OCR text contains standardized test score language.",
            ),
            (
                "application_form",
                ("application form", "applicant information", "program applied", "intake term"),
                0.88,
                "OCR text contains application form language.",
            ),
        )
        for document_type, phrases, confidence, rationale in rules:
            matched = [phrase for phrase in phrases if phrase in text]
            if matched:
                return ClassificationResult(
                    document_type=document_type,
                    confidence=confidence,
                    rationale=rationale,
                    evidence_snippets=matched[:3],
                    requires_human_review=False,
                    source="rule_based",
                )
        return None


class MockClassificationService:
    def __init__(self, result: ClassificationResult | None = None) -> None:
        self.result = result or ClassificationResult(
            document_type="personal_statement",
            confidence=0.91,
            rationale="Mock classifier recognized personal statement content.",
            evidence_snippets=["mock personal statement evidence"],
            requires_human_review=False,
            source="mock_model",
        )
        self.calls = 0

    def classify(self, payload: ClassificationPayload) -> ClassificationResult:
        self.calls += 1
        return self.result


class AzureOpenAIClassificationService:
    def __init__(self, settings: AppSettings) -> None:
        if not settings.azure_openai_endpoint:
            raise ClassificationServiceError("Azure OpenAI endpoint is not configured.")
        if not settings.azure_openai_api_key:
            raise ClassificationServiceError("Azure OpenAI API key is not configured.")
        if not settings.azure_openai_api_version:
            raise ClassificationServiceError("Azure OpenAI API version is not configured.")

        deployment = settings.azure_openai_classification_model_deployment
        if not deployment:
            raise ClassificationServiceError("Azure OpenAI classification deployment is not configured.")

        self.settings = settings
        self.client = create_azure_openai_chat_client(settings)
        self.deployment = deployment

    def classify(self, payload: ClassificationPayload) -> ClassificationResult:
        try:
            response = self.client.chat.completions.create(
                **chat_completion_kwargs(
                    model=self.deployment,
                    messages=[
                        {
                            "role": "user",
                            "content": _build_llm_prompt(payload),
                        },
                    ],
                    response_format=classification_response_format(),
                    settings=self.settings,
                )
            )
            content = response.choices[0].message.content
            parsed = LLMClassificationResult.model_validate_json(content or "{}")
            return ClassificationResult(
                **parsed.model_dump(),
                source="azure_openai",
            )
        except (ValidationError, json.JSONDecodeError, IndexError, AttributeError) as exc:
            raise ClassificationServiceError("Azure OpenAI returned invalid classification JSON.") from exc
        except Exception as exc:
            raise ClassificationServiceError("Azure OpenAI classification failed.") from exc


class TwoStageClassificationService:
    def __init__(
        self,
        *,
        db: Session,
        settings: AppSettings,
        fallback_classifier: ClassificationService,
        rule_classifier: ClassificationService | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.fallback_classifier = fallback_classifier
        self.rule_classifier = rule_classifier or RuleBasedClassificationService()

    def classify_document(self, document_id: str) -> ClassificationResult:
        document = self.db.get(ApplicationDocument, document_id)
        if document is None:
            raise ClassificationDocumentNotFoundError("Document not found.")

        extraction = self.db.get(ExtractedDocumentContent, document_id)
        payload = build_classification_payload(
            document=document,
            extraction=extraction,
            max_chars=self.settings.classification_text_max_chars,
        )

        rule_result = self.rule_classifier.classify(payload)
        if _is_decisive_rule_result(rule_result, self.settings.rule_based_classification_min_confidence):
            return _with_human_review_threshold(
                rule_result,
                self.settings.classification_low_confidence_threshold,
            )

        if extraction is None or not payload.text.strip():
            return ClassificationResult(
                document_type=LOW_CONFIDENCE_TYPE,
                confidence=0.15,
                rationale="No extracted OCR text is available for ambiguous document classification.",
                evidence_snippets=[],
                requires_human_review=True,
                source="rule_based",
            )

        fallback_result = self.fallback_classifier.classify(payload)
        return _with_human_review_threshold(
            fallback_result,
            self.settings.classification_low_confidence_threshold,
        )


def get_document_classification_service(
    *,
    db: Session,
    settings: AppSettings,
) -> TwoStageClassificationService:
    backend = settings.document_classification_backend.strip().lower()
    if backend == "mock":
        fallback: ClassificationService = MockClassificationService()
    elif backend == "azure":
        fallback = AzureOpenAIClassificationService(settings)
    else:
        raise ClassificationServiceError(f"Unsupported classification backend: {backend}.")

    return TwoStageClassificationService(
        db=db,
        settings=settings,
        fallback_classifier=fallback,
    )


def build_classification_payload(
    *,
    document: ApplicationDocument,
    extraction: ExtractedDocumentContent | None,
    max_chars: int,
) -> ClassificationPayload:
    if extraction is None:
        return ClassificationPayload(filename=document.original_filename)

    text_parts = []
    for page in (extraction.pages or [])[:3]:
        page_text = str(page.get("text") or "").strip()
        if page_text:
            text_parts.append(page_text)

    text = "\n\n".join(text_parts).strip()
    if not text:
        text = (extraction.raw_text or "").strip()
    text = text[:max_chars]

    return ClassificationPayload(
        filename=document.original_filename,
        text=text,
        detected_headings=detect_headings(extraction.pages or [], max_headings=12),
    )


def detect_headings(pages: list[dict], *, max_headings: int) -> list[str]:
    headings: list[str] = []
    for page in pages[:3]:
        for line in page.get("lines", []):
            content = str(line.get("content") or "").strip()
            if _looks_like_heading(content):
                headings.append(content[:120])
            if len(headings) >= max_headings:
                return headings
    return headings


def classification_response_format() -> dict:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "DocumentClassificationResult",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "document_type": {
                        "type": "string",
                        "enum": list(SUPPORTED_DOCUMENT_TYPES),
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "rationale": {"type": "string"},
                    "evidence_snippets": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "requires_human_review": {"type": "boolean"},
                },
                "required": [
                    "document_type",
                    "confidence",
                    "rationale",
                    "evidence_snippets",
                    "requires_human_review",
                ],
            },
        },
    }


def _build_llm_prompt(payload: ClassificationPayload) -> str:
    template = load_prompt("document_classification")
    return template.render(
        {
            "filename": payload.filename,
            "detected_headings": payload.detected_headings,
            "ocr_excerpt": payload.text,
        }
    )


def document_classification_prompt_metadata() -> dict:
    return prompt_identity("document_classification")


def _is_decisive_rule_result(result: ClassificationResult, threshold: float) -> bool:
    return (
        result.source == "rule_based"
        and result.document_type != LOW_CONFIDENCE_TYPE
        and result.confidence >= threshold
    )


def _with_human_review_threshold(result: ClassificationResult, threshold: float) -> ClassificationResult:
    return result.model_copy(
        update={
            "requires_human_review": (
                result.requires_human_review
                or result.confidence < threshold
                or result.document_type == LOW_CONFIDENCE_TYPE
            )
        }
    )


def _looks_like_heading(value: str) -> bool:
    if not value or len(value) > 120:
        return False
    lowered = value.lower()
    heading_keywords = (
        "personal statement",
        "statement of purpose",
        "education",
        "experience",
        "skills",
        "transcript",
        "recommendation",
        "test score",
        "applicant information",
        "academic record",
    )
    return (
        any(keyword in lowered for keyword in heading_keywords)
        or (value.isupper() and 4 <= len(value) <= 80)
    )


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()
