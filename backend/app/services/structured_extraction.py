from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.config import AppSettings
from app.models import ApplicationDocument, ExtractedDocumentContent
from app.schemas.structured_extractions import (
    STRUCTURED_EXTRACTION_SCHEMAS,
    EvidenceSnippet,
    NumberField,
    PersonalStatementExtraction,
    RecommendationLetterExtraction,
    ResumeExtraction,
    StrictSchema,
    TestScoreExtraction,
    TextField,
    TextListField,
    TranscriptExtraction,
)
from app.services.azure_openai import chat_completion_kwargs, create_azure_openai_chat_client
from app.services.classification import detect_headings
from app.services.prompts import load_prompt


logger = logging.getLogger(__name__)

STRUCTURED_PROMPTS_BY_DOCUMENT_TYPE: dict[str, str] = {
    "transcript": "transcript_extraction",
    "personal_statement": "personal_statement_extraction",
    "recommendation_letter": "recommendation_extraction",
    "resume_cv": "resume_extraction",
    "test_score_report": "test_score_extraction",
}


class StructuredExtractionError(Exception):
    """Base error for structured extraction failures."""


class StructuredExtractionDocumentNotFoundError(StructuredExtractionError):
    pass


class StructuredExtractionRequiresOcrError(StructuredExtractionError):
    pass


class UnsupportedStructuredExtractionTypeError(StructuredExtractionError):
    pass


class StructuredExtractionServiceError(StructuredExtractionError):
    pass


@dataclass(frozen=True)
class StructuredExtractionPayload:
    filename: str
    document_type: str
    text: str
    detected_headings: list[str]


@dataclass(frozen=True)
class StructuredExtractionResult:
    document_id: str
    document_type: str
    extraction: StrictSchema
    metadata: dict


class StructuredExtractionService(Protocol):
    def structured_extract(self, document_id: str) -> StructuredExtractionResult:
        raise NotImplementedError


class BaseStructuredExtractionService:
    def __init__(self, *, db: Session, settings: AppSettings) -> None:
        self.db = db
        self.settings = settings

    def structured_extract(self, document_id: str) -> StructuredExtractionResult:
        document = self.db.get(ApplicationDocument, document_id)
        if document is None:
            raise StructuredExtractionDocumentNotFoundError("Document not found.")

        document_type = document.document_type
        if document_type not in STRUCTURED_EXTRACTION_SCHEMAS:
            raise UnsupportedStructuredExtractionTypeError(
                f"Structured extraction is not supported for document_type '{document_type or 'unclassified'}'."
            )

        extracted_content = self.db.get(ExtractedDocumentContent, document_id)
        if extracted_content is None:
            raise StructuredExtractionRequiresOcrError("OCR extraction is required before structured extraction.")

        payload = build_structured_extraction_payload(
            document=document,
            extracted_content=extracted_content,
            max_chars=self.settings.structured_extraction_text_max_chars,
        )
        schema = STRUCTURED_EXTRACTION_SCHEMAS[document_type]
        extraction = self.extract_for_schema(payload=payload, schema=schema)
        return StructuredExtractionResult(
            document_id=document_id,
            document_type=document_type,
            extraction=extraction,
            metadata=self.metadata(document_type=document_type, schema=schema),
        )

    def extract_for_schema(
        self,
        *,
        payload: StructuredExtractionPayload,
        schema: type[StrictSchema],
    ) -> StrictSchema:
        raise NotImplementedError

    def metadata(self, *, document_type: str, schema: type[StrictSchema]) -> dict:
        prompt = structured_prompt_template(document_type)
        return {
            **prompt.identity(),
            "source": "unknown",
            "document_type": document_type,
            "schema": schema.__name__,
        }


class MockStructuredExtractionService(BaseStructuredExtractionService):
    def __init__(
        self,
        *,
        db: Session,
        settings: AppSettings,
        extraction: StrictSchema | None = None,
    ) -> None:
        super().__init__(db=db, settings=settings)
        self.extraction = extraction

    def extract_for_schema(
        self,
        *,
        payload: StructuredExtractionPayload,
        schema: type[StrictSchema],
    ) -> StrictSchema:
        if self.extraction is not None:
            return schema.model_validate(self.extraction.model_dump())
        return mock_extraction_for_type(payload.document_type)

    def metadata(self, *, document_type: str, schema: type[StrictSchema]) -> dict:
        prompt = structured_prompt_template(document_type)
        return {
            **prompt.identity(),
            "source": "mock",
            "document_type": document_type,
            "schema": schema.__name__,
        }


class AzureOpenAIStructuredExtractionService(BaseStructuredExtractionService):
    def __init__(self, *, db: Session, settings: AppSettings) -> None:
        super().__init__(db=db, settings=settings)
        if not settings.azure_openai_endpoint:
            raise StructuredExtractionServiceError("Azure OpenAI endpoint is not configured.")
        if not settings.azure_openai_api_key:
            raise StructuredExtractionServiceError("Azure OpenAI API key is not configured.")
        if not settings.azure_openai_api_version:
            raise StructuredExtractionServiceError("Azure OpenAI API version is not configured.")
        deployment = settings.azure_openai_structured_extraction_model_deployment
        if not deployment:
            raise StructuredExtractionServiceError(
                "Azure OpenAI structured extraction deployment is not configured."
            )

        self.settings = settings
        self.client = create_azure_openai_chat_client(settings)
        self.deployment = deployment

    def extract_for_schema(
        self,
        *,
        payload: StructuredExtractionPayload,
        schema: type[StrictSchema],
    ) -> StrictSchema:
        try:
            response = self.client.chat.completions.create(
                **chat_completion_kwargs(
                    model=self.deployment,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "Extract structured admissions document facts from OCR text. "
                                "Use only supplied text. Include evidence for important fields. "
                                "When evidence is absent, mark the field uncertain with an explanation. "
                                "Never invent facts and never make an admissions decision."
                            ),
                        },
                        {
                            "role": "user",
                            "content": build_structured_extraction_prompt(payload),
                        },
                    ],
                    response_format=structured_extraction_response_format(schema),
                    settings=self.settings,
                )
            )
            content = response.choices[0].message.content
            return schema.model_validate_json(content or "{}")
        except (ValidationError, IndexError, AttributeError) as exc:
            raise StructuredExtractionServiceError(
                "Azure OpenAI returned invalid structured extraction JSON."
            ) from exc
        except Exception as exc:
            raise StructuredExtractionServiceError("Azure OpenAI structured extraction failed.") from exc

    def metadata(self, *, document_type: str, schema: type[StrictSchema]) -> dict:
        prompt = structured_prompt_template(document_type)
        return {
            **prompt.identity(),
            "source": "azure_openai",
            "document_type": document_type,
            "schema": schema.__name__,
            "model_deployment": self.deployment,
        }


def get_structured_extraction_service(
    *,
    db: Session,
    settings: AppSettings,
) -> StructuredExtractionService:
    backend = settings.structured_extraction_backend.strip().lower()
    if backend == "mock":
        return MockStructuredExtractionService(db=db, settings=settings)
    if backend == "azure":
        return AzureOpenAIStructuredExtractionService(db=db, settings=settings)
    raise StructuredExtractionServiceError(f"Unsupported structured extraction backend: {backend}.")


def build_structured_extraction_payload(
    *,
    document: ApplicationDocument,
    extracted_content: ExtractedDocumentContent,
    max_chars: int,
) -> StructuredExtractionPayload:
    text_parts = []
    for page in (extracted_content.pages or [])[:3]:
        page_text = str(page.get("text") or "").strip()
        if page_text:
            text_parts.append(f"Page {page.get('page_number') or '?'}:\n{page_text}")

    text = "\n\n".join(text_parts).strip()
    if not text:
        text = (extracted_content.raw_text or "").strip()

    return StructuredExtractionPayload(
        filename=document.original_filename,
        document_type=document.document_type or "other_or_unreadable",
        text=text[:max_chars],
        detected_headings=detect_headings(extracted_content.pages or [], max_headings=16),
    )


def structured_extraction_response_format(schema: type[StrictSchema]) -> dict:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema.__name__,
            "strict": True,
            "schema": schema.model_json_schema(),
        },
    }


def build_structured_extraction_prompt(payload: StructuredExtractionPayload) -> str:
    template = structured_prompt_template(payload.document_type)
    return template.render(
        {
            "filename": payload.filename,
            "document_type": payload.document_type,
            "detected_headings": payload.detected_headings,
            "ocr_excerpt": payload.text,
        }
    )


def structured_prompt_template(document_type: str):
    prompt_name = STRUCTURED_PROMPTS_BY_DOCUMENT_TYPE.get(document_type)
    if prompt_name is None:
        raise UnsupportedStructuredExtractionTypeError(
            f"Structured extraction prompt is not supported for document_type '{document_type}'."
        )
    return load_prompt(prompt_name)


def mock_extraction_for_type(document_type: str) -> StrictSchema:
    evidence = [EvidenceSnippet(snippet="Mock evidence from OCR text.", page_number=1, source="mock")]
    uncertain_text = TextField(
        value=None,
        evidence=[],
        uncertain=True,
        explanation="Mock extractor did not find supporting evidence.",
    )
    uncertain_list = TextListField(
        value=[],
        evidence=[],
        uncertain=True,
        explanation="Mock extractor did not find supporting evidence.",
    )
    if document_type == "transcript":
        return TranscriptExtraction(
            institution=TextField(value="Mock University", evidence=evidence, uncertain=False, explanation=None),
            degree=TextField(value="MSc", evidence=evidence, uncertain=False, explanation=None),
            major=TextField(value="Computer Science", evidence=evidence, uncertain=False, explanation=None),
            gpa=NumberField(value=3.8, evidence=evidence, uncertain=False, explanation=None),
            gpa_scale=NumberField(value=4.0, evidence=evidence, uncertain=False, explanation=None),
            coursework_highlights=TextListField(value=["Algorithms"], evidence=evidence, uncertain=False, explanation=None),
            academic_honors=uncertain_list,
            academic_risks=uncertain_list,
            evidence=evidence,
        )
    if document_type == "personal_statement":
        return PersonalStatementExtraction(
            applicant_goals=TextListField(value=["Graduate study"], evidence=evidence, uncertain=False, explanation=None),
            motivation=TextField(value="Academic growth", evidence=evidence, uncertain=False, explanation=None),
            program_fit=TextField(value="Aligned with program", evidence=evidence, uncertain=False, explanation=None),
            academic_interests=TextListField(value=["AI"], evidence=evidence, uncertain=False, explanation=None),
            career_goals=TextField(value="Research career", evidence=evidence, uncertain=False, explanation=None),
            writing_quality_observations=TextListField(value=["Clear structure"], evidence=evidence, uncertain=False, explanation=None),
            notable_strengths=TextListField(value=["Focused goals"], evidence=evidence, uncertain=False, explanation=None),
            concerns_or_gaps=uncertain_list,
            evidence=evidence,
        )
    if document_type == "recommendation_letter":
        return RecommendationLetterExtraction(
            recommender_name=uncertain_text,
            recommender_role=uncertain_text,
            relationship_to_applicant=TextField(value="Instructor", evidence=evidence, uncertain=False, explanation=None),
            recommendation_strength=TextField(value="Strong", evidence=evidence, uncertain=False, explanation=None),
            academic_ability=TextField(value="High", evidence=evidence, uncertain=False, explanation=None),
            character_traits=TextListField(value=["Collaborative"], evidence=evidence, uncertain=False, explanation=None),
            leadership_or_teamwork=TextField(value="Team contributor", evidence=evidence, uncertain=False, explanation=None),
            concerns_or_caveats=uncertain_list,
            evidence=evidence,
        )
    if document_type == "resume_cv":
        return ResumeExtraction(
            education=TextListField(value=["Mock University"], evidence=evidence, uncertain=False, explanation=None),
            work_experience=uncertain_list,
            projects=TextListField(value=["Admissions analytics project"], evidence=evidence, uncertain=False, explanation=None),
            leadership=uncertain_list,
            awards=uncertain_list,
            skills=TextListField(value=["Python"], evidence=evidence, uncertain=False, explanation=None),
            evidence=evidence,
        )
    if document_type == "test_score_report":
        from app.schemas.structured_extractions import ScoreComponent, ScoreComponentsField

        return TestScoreExtraction(
            test_name=TextField(value="Mock Test", evidence=evidence, uncertain=False, explanation=None),
            score=NumberField(value=100.0, evidence=evidence, uncertain=False, explanation=None),
            score_components=ScoreComponentsField(
                value=[
                    ScoreComponent(
                        name="Overall",
                        score=100.0,
                        evidence=evidence,
                        uncertain=False,
                        explanation=None,
                    )
                ],
                evidence=evidence,
                uncertain=False,
                explanation=None,
            ),
            test_date=uncertain_text,
            evidence=evidence,
        )
    raise UnsupportedStructuredExtractionTypeError(
        f"Structured extraction is not supported for document_type '{document_type}'."
    )
