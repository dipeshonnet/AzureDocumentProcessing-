from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import AppSettings
from app.models import (
    Application,
    ApplicationDocument,
    DocumentSummary,
    ExtractedDocumentContent,
    IntakeJob,
    RubricCriterionScore,
    RubricScorecard,
    SavedRubric,
)
from app.schemas.rubrics import AdmissionsRubric, RubricCriterion
from app.services.azure_openai import chat_completion_kwargs, create_azure_openai_chat_client
from app.services.prompts import load_prompt
from app.services.rubrics import RubricLoadError, load_default_rubric
from app.services.rubric_store import get_saved_rubric


logger = logging.getLogger(__name__)


PROTECTED_ATTRIBUTE_PATTERNS: tuple[str, ...] = (
    r"\brace\b",
    r"\breligion\b",
    r"\bgender\b",
    r"\bnationality\b",
    r"\bdisability\b",
    r"\bage\b",
    r"\bethnicity\b",
    r"\bmale\b",
    r"\bfemale\b",
    r"\bchristian\b",
    r"\bmuslim\b",
    r"\bhindu\b",
    r"\bjewish\b",
)


class RubricScoringError(Exception):
    """Base error for rubric scoring failures."""


class RubricScoringApplicationNotFoundError(RubricScoringError):
    pass


class RubricScoringServiceError(RubricScoringError):
    pass


class RubricCriterionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snippet: str = Field(min_length=1)
    document_id: str | None
    page_number: int | None = Field(..., ge=1)
    source: str | None


class RubricCriterionScoreResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion_id: str
    criterion_name: str
    score: float = Field(ge=0)
    max_score: float = Field(gt=0)
    rationale: str = Field(min_length=1)
    supporting_evidence: list[RubricCriterionEvidence]
    missing_information: list[str]
    confidence: float = Field(ge=0, le=1)
    requires_human_review: bool
    risk_flags: list[str]

    @model_validator(mode="after")
    def validate_score_and_evidence(self) -> RubricCriterionScoreResult:
        if self.score > self.max_score:
            raise ValueError("score cannot exceed max_score")
        if not self.supporting_evidence and not self.missing_information:
            raise ValueError("missing evidence must be represented in missing_information")
        return self


@dataclass(frozen=True)
class RubricScoringContext:
    application_id: str
    criterion: RubricCriterion
    rubric: AdmissionsRubric
    evidence_text: str
    protected_attribute_text_present: bool


@dataclass(frozen=True)
class RubricScoringOutcome:
    rubric: AdmissionsRubric
    criteria: list[RubricCriterionScoreResult]
    weighted_score: float
    max_score: float
    recommendation_band: str
    decision_support_summary: str
    requires_human_review: bool
    risk_flags: list[str]


class RubricScoringService(Protocol):
    def score_application(self, application_id: str) -> RubricScoringOutcome:
        raise NotImplementedError


class BaseRubricScoringService:
    def __init__(self, *, db: Session, settings: AppSettings) -> None:
        self.db = db
        self.settings = settings

    def score_application(self, application_id: str) -> RubricScoringOutcome:
        application = self.db.get(Application, application_id)
        if application is None:
            raise RubricScoringApplicationNotFoundError("Application not found.")

        try:
            rubric = resolve_application_scoring_rubric(db=self.db, application_id=application_id)
        except RubricLoadError as exc:
            raise RubricScoringServiceError("Application rubric could not be loaded.") from exc

        apply_rubric_section_alignment(db=self.db, application_id=application_id, rubric=rubric)

        full_evidence_text = build_application_evidence_text(
            db=self.db,
            application_id=application_id,
            max_chars=self.settings.rubric_scoring_text_max_chars,
        )
        protected_attribute_text_present = contains_protected_attribute_text(full_evidence_text)
        results = [
            self.score_criterion(
                RubricScoringContext(
                    application_id=application_id,
                    criterion=criterion,
                    rubric=rubric,
                    evidence_text=build_application_evidence_text(
                        db=self.db,
                        application_id=application_id,
                        max_chars=self.settings.rubric_scoring_text_max_chars,
                        criterion=criterion,
                    ),
                    protected_attribute_text_present=protected_attribute_text_present,
                )
            )
            for criterion in rubric.criteria
        ]
        results = [
            apply_scoring_safeguards(
                result,
                low_confidence_threshold=self.settings.rubric_scoring_low_confidence_threshold,
                protected_attribute_text_present=protected_attribute_text_present,
            )
            for result in results
        ]
        return calculate_scorecard(results=results, rubric=rubric)

    def score_criterion(self, context: RubricScoringContext) -> RubricCriterionScoreResult:
        raise NotImplementedError


class MockRubricScoringService(BaseRubricScoringService):
    def __init__(
        self,
        *,
        db: Session,
        settings: AppSettings,
        confidence: float = 0.82,
    ) -> None:
        super().__init__(db=db, settings=settings)
        self.confidence = confidence

    def score_criterion(self, context: RubricScoringContext) -> RubricCriterionScoreResult:
        has_evidence = bool(context.evidence_text.strip())
        evidence = []
        missing = []
        score = 3.0
        rationale = "Evidence supports a moderate advisory score for this criterion."
        confidence = self.confidence
        requires_human_review = False
        if has_evidence:
            evidence = [
                RubricCriterionEvidence(
                    snippet=context.evidence_text[:180],
                    document_id=None,
                    page_number=1,
                    source="mock",
                )
            ]
        else:
            score = 1.0
            confidence = min(confidence, 0.3)
            rationale = "Insufficient evidence is available for this criterion."
            missing = list(context.criterion.evidence_required)
            requires_human_review = True

        return RubricCriterionScoreResult(
            criterion_id=context.criterion.criterion_id,
            criterion_name=context.criterion.name,
            score=score,
            max_score=context.criterion.max_score,
            rationale=rationale,
            supporting_evidence=evidence,
            missing_information=missing,
            confidence=confidence,
            requires_human_review=requires_human_review,
            risk_flags=[],
        )


class AzureOpenAIRubricScoringService(BaseRubricScoringService):
    def __init__(self, *, db: Session, settings: AppSettings) -> None:
        super().__init__(db=db, settings=settings)
        if not settings.azure_openai_endpoint:
            raise RubricScoringServiceError("Azure OpenAI endpoint is not configured.")
        if not settings.azure_openai_api_key:
            raise RubricScoringServiceError("Azure OpenAI API key is not configured.")
        if not settings.azure_openai_api_version:
            raise RubricScoringServiceError("Azure OpenAI API version is not configured.")
        deployment = settings.azure_openai_rubric_scoring_model_deployment
        if not deployment:
            raise RubricScoringServiceError("Azure OpenAI rubric scoring deployment is not configured.")

        self.settings = settings
        self.client = create_azure_openai_chat_client(settings)
        self.deployment = deployment

    def score_criterion(self, context: RubricScoringContext) -> RubricCriterionScoreResult:
        try:
            response = self.client.chat.completions.create(
                **chat_completion_kwargs(
                    model=self.deployment,
                    messages=[
                        {
                            "role": "user",
                            "content": build_rubric_scoring_prompt(context),
                        },
                    ],
                    response_format=rubric_scoring_response_format(),
                    settings=self.settings,
                )
            )
            content = response.choices[0].message.content
            return RubricCriterionScoreResult.model_validate_json(content or "{}")
        except (ValidationError, IndexError, AttributeError) as exc:
            raise RubricScoringServiceError("Azure OpenAI returned invalid rubric scoring JSON.") from exc
        except Exception as exc:
            raise RubricScoringServiceError("Azure OpenAI rubric scoring failed.") from exc


def get_rubric_scoring_service(*, db: Session, settings: AppSettings) -> RubricScoringService:
    backend = settings.rubric_scoring_backend.strip().lower()
    if backend == "mock":
        return MockRubricScoringService(db=db, settings=settings)
    if backend == "azure":
        return AzureOpenAIRubricScoringService(db=db, settings=settings)
    raise RubricScoringServiceError(f"Unsupported rubric scoring backend: {backend}.")


def build_application_evidence_text(
    *,
    db: Session,
    application_id: str,
    max_chars: int,
    criterion: RubricCriterion | None = None,
) -> str:
    documents = list(
        db.scalars(
            select(ApplicationDocument)
            .where(ApplicationDocument.application_id == application_id)
            .order_by(ApplicationDocument.created_at)
        )
    )
    if criterion is not None:
        has_rubric_alignments = any(
            isinstance((document.classification_metadata or {}).get("rubric_section_matches"), list)
            for document in documents
        )
        matching_documents = [
            document
            for document in documents
            if document_matches_rubric_criterion(document=document, criterion_id=criterion.criterion_id)
        ]
        if matching_documents:
            documents = matching_documents
        elif has_rubric_alignments:
            return ""

    parts: list[str] = []
    for document in documents:
        parts.append(f"Document {document.document_id} ({document.document_type or 'unclassified'}): {document.original_filename}")
        matches = (document.classification_metadata or {}).get("rubric_section_matches")
        if matches:
            parts.append(f"Rubric section matches: {matches}")
        if document.summary:
            parts.append(f"Summary: {document.summary.short_summary}")
            if document.summary.strengths:
                parts.append(f"Strengths: {', '.join(document.summary.strengths)}")
            if document.summary.concerns:
                parts.append(f"Concerns: {', '.join(document.summary.concerns)}")
            if document.summary.missing_information:
                parts.append(f"Missing information: {', '.join(document.summary.missing_information)}")
        if document.extracted_content:
            structured = document.extracted_content.structured_extraction or {}
            if structured:
                parts.append(f"Structured extraction: {structured}")
            raw_text = (document.extracted_content.raw_text or "").strip()
            if raw_text:
                parts.append(f"OCR excerpt: {raw_text[:1200]}")
    return "\n\n".join(parts)[:max_chars]


def resolve_application_scoring_rubric(*, db: Session, application_id: str) -> AdmissionsRubric:
    rubric_id = db.scalar(
        select(IntakeJob.rubric_id)
        .where(IntakeJob.application_id == application_id)
        .order_by(IntakeJob.received_at.desc())
        .limit(1)
    )
    if rubric_id:
        saved_rubric = get_saved_rubric(db, rubric_id)
        if saved_rubric is not None:
            return saved_rubric_to_admissions_rubric(saved_rubric)
    return load_default_rubric()


def saved_rubric_to_admissions_rubric(saved_rubric: SavedRubric) -> AdmissionsRubric:
    sections = list(saved_rubric.sections or [])
    section_points = [
        max(float(section.get("max_points") or 0), 0.0)
        for section in sections
    ]
    total_points = sum(section_points)
    if total_points <= 0:
        total_points = float(len(sections) or 1)

    criteria = []
    for index, section in enumerate(sections):
        section_id = str(section.get("section_id") or f"section_{index + 1}").strip()
        name = str(section.get("name") or section_id).strip()
        max_score = max(float(section.get("max_points") or 0), 1.0)
        criteria.append(
            RubricCriterion(
                criterion_id=section_id,
                name=name,
                description=serialize_saved_rubric_section(section),
                weight=(max_score / total_points) * 100,
                max_score=max_score,
                scoring_levels={
                    1: "Little or no rubric-aligned evidence is present.",
                    2: "Limited evidence is present but does not satisfy most configured rows.",
                    3: "Adequate evidence satisfies some configured rows or conditions.",
                    4: "Strong evidence satisfies most configured rows or conditions.",
                    5: "Excellent evidence satisfies the strongest applicable configured rows or conditions.",
                },
                evidence_required=evidence_required_for_saved_section(section),
                human_review_triggers=[
                    "No document is categorized for this rubric section.",
                    "Evidence is insufficient to apply the configured rows.",
                    "The rubric row points or conditions are ambiguous.",
                    "Protected attribute-like text appears and must be ignored for scoring.",
                ],
            )
        )

    return AdmissionsRubric(
        rubric_id=saved_rubric.rubric_id,
        name=saved_rubric.name,
        description=saved_rubric.description or "Saved admissions scoring rubric.",
        version=saved_rubric.version,
        criteria=criteria,
    )


def serialize_saved_rubric_section(section: dict[str, Any]) -> str:
    lines = [
        f"Section: {section.get('name') or section.get('section_id')}",
        f"Section description: {section.get('description') or 'No section description provided.'}",
        f"Section max points: {section.get('max_points') or 0}",
        "Use the configured rows below as the scoring instructions for this section.",
    ]
    for component in section.get("components") or []:
        lines.append(
            f"Component: {component.get('name') or component.get('component_id')} "
            f"(max {component.get('max_points') or 0})"
        )
        if component.get("description"):
            lines.append(f"Component description: {component.get('description')}")
        for row in component.get("rows") or []:
            lines.append(
                "Row: "
                f"label={row.get('label') or ''}; "
                f"condition={row.get('condition') or ''}; "
                f"points={row.get('points') or ''}; "
                f"notes={row.get('notes') or ''}"
            )
    return "\n".join(lines)


def evidence_required_for_saved_section(section: dict[str, Any]) -> list[str]:
    values = [
        str(section.get("name") or section.get("section_id") or "rubric section evidence")
    ]
    for component in section.get("components") or []:
        name = str(component.get("name") or "").strip()
        if name:
            values.append(name)
    return values[:8]


def apply_rubric_section_alignment(*, db: Session, application_id: str, rubric: AdmissionsRubric) -> None:
    documents = list(
        db.scalars(
            select(ApplicationDocument)
            .where(ApplicationDocument.application_id == application_id)
            .order_by(ApplicationDocument.created_at)
        )
    )
    for document in documents:
        metadata = dict(document.classification_metadata or {})
        metadata["rubric_id"] = rubric.rubric_id
        metadata["rubric_section_matches"] = rubric_section_matches_for_document(
            document=document,
            rubric=rubric,
        )
        document.classification_metadata = metadata


def rubric_section_matches_for_document(
    *,
    document: ApplicationDocument,
    rubric: AdmissionsRubric,
) -> list[dict[str, Any]]:
    document_type = (document.document_type or "").lower()
    primary_keywords, fallback_keywords = section_keywords_for_document_type(document_type)
    primary_matches = matching_rubric_criteria(
        rubric=rubric,
        keywords=primary_keywords,
        confidence=0.95,
        rationale=f"Rubric section directly matches document type '{document_type}'.",
        evidence_snippets=(document.classification_metadata or {}).get("evidence_snippets") or [],
    )
    if primary_matches:
        return primary_matches
    return matching_rubric_criteria(
        rubric=rubric,
        keywords=fallback_keywords,
        confidence=0.72,
        rationale=f"Rubric section is a fallback match for document type '{document_type}'.",
        evidence_snippets=(document.classification_metadata or {}).get("evidence_snippets") or [],
    )


def section_keywords_for_document_type(document_type: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if document_type == "personal_statement":
        return ("personal statement", "statement of purpose"), ("short answer", "essay", "writing")
    if document_type == "recommendation_letter":
        return ("recommendation", "letter of recommendation", "reference", "lor"), ()
    if document_type == "transcript":
        return ("transcript", "gpa", "academic", "coursework", "prerequisite"), ()
    if document_type == "resume_cv":
        return ("resume", "cv", "experience", "leadership", "work"), ()
    if document_type == "test_score_report":
        return ("test score", "score report", "gre", "gmat", "toefl", "ielts", "sat", "act"), ()
    if document_type == "application_form":
        return ("application form", "applicant information", "program"), ()
    return (), ()


def matching_rubric_criteria(
    *,
    rubric: AdmissionsRubric,
    keywords: tuple[str, ...],
    confidence: float,
    rationale: str,
    evidence_snippets: list[str],
) -> list[dict[str, Any]]:
    if not keywords:
        return []
    matches = []
    for criterion in rubric.criteria:
        haystack = " ".join(
            [
                criterion.criterion_id,
                criterion.name,
                criterion.description,
            ]
        ).lower()
        matched = [keyword for keyword in keywords if keyword in haystack]
        if matched:
            matches.append(
                {
                    "rubric_section_id": criterion.criterion_id,
                    "rubric_section_name": criterion.name,
                    "confidence": confidence,
                    "rationale": rationale,
                    "matched_terms": matched,
                    "evidence": evidence_snippets[:3],
                }
            )
    return matches


def document_matches_rubric_criterion(*, document: ApplicationDocument, criterion_id: str) -> bool:
    matches = (document.classification_metadata or {}).get("rubric_section_matches") or []
    return any(
        isinstance(match, dict)
        and match.get("rubric_section_id") == criterion_id
        for match in matches
    )


def calculate_scorecard(
    *,
    results: list[RubricCriterionScoreResult],
    rubric: AdmissionsRubric,
) -> RubricScoringOutcome:
    weights = {criterion.criterion_id: criterion.weight for criterion in rubric.criteria}
    total_weight = sum(weights.values())
    normalizer = 100.0 if abs(total_weight - 100.0) <= 0.0001 else total_weight
    weighted_score = 0.0
    max_score = 0.0
    for result in results:
        normalized_weight = weights[result.criterion_id] / normalizer
        weighted_score += result.score * normalized_weight
        max_score += result.max_score * normalized_weight

    risk_flags = sorted({flag for result in results for flag in result.risk_flags})
    requires_human_review = any(result.requires_human_review for result in results) or bool(risk_flags)
    recommendation_band = recommendation_band_for(
        weighted_score=weighted_score,
        max_score=max_score,
        requires_human_review=requires_human_review,
    )
    return RubricScoringOutcome(
        rubric=rubric,
        criteria=results,
        weighted_score=round(weighted_score, 4),
        max_score=round(max_score, 4),
        recommendation_band=recommendation_band,
        decision_support_summary=(
            "Rubric scores are advisory decision support only and require authorized human review."
        ),
        requires_human_review=requires_human_review,
        risk_flags=risk_flags,
    )


def recommendation_band_for(*, weighted_score: float, max_score: float, requires_human_review: bool) -> str:
    if requires_human_review:
        return "human_review_required"
    ratio = weighted_score / max_score if max_score else 0
    if ratio >= 0.8:
        return "strong_evidence_support"
    if ratio >= 0.6:
        return "moderate_evidence_support"
    return "limited_evidence_support"


def apply_scoring_safeguards(
    result: RubricCriterionScoreResult,
    *,
    low_confidence_threshold: float,
    protected_attribute_text_present: bool,
) -> RubricCriterionScoreResult:
    risk_flags = list(result.risk_flags)
    if protected_attribute_text_present and "protected_attribute_text_present_do_not_use_for_scoring" not in risk_flags:
        risk_flags.append("protected_attribute_text_present_do_not_use_for_scoring")
    if not result.supporting_evidence and "missing_supporting_evidence" not in risk_flags:
        risk_flags.append("missing_supporting_evidence")
    return result.model_copy(
        update={
            "requires_human_review": (
                result.requires_human_review
                or result.confidence < low_confidence_threshold
                or protected_attribute_text_present
                or not result.supporting_evidence
            ),
            "risk_flags": risk_flags,
        }
    )


def contains_protected_attribute_text(value: str) -> bool:
    lowered = value.lower()
    return any(re.search(pattern, lowered) for pattern in PROTECTED_ATTRIBUTE_PATTERNS)


def rubric_scoring_response_format() -> dict:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "RubricCriterionScoreResult",
            "strict": True,
            "schema": RubricCriterionScoreResult.model_json_schema(),
        },
    }


def build_rubric_scoring_prompt(context: RubricScoringContext) -> str:
    criterion = context.criterion
    levels = "\n".join(
        f"{level}: {description}" for level, description in sorted(criterion.scoring_levels.items())
    )
    protected_note = (
        "Protected attribute-like text may be present. Ignore it for scoring and flag it."
        if context.protected_attribute_text_present
        else "No protected attribute-like text was detected by preprocessing."
    )
    template = load_prompt("rubric_scoring")
    return template.render(
        {
            "application_id": context.application_id,
            "criterion_id": criterion.criterion_id,
            "criterion_name": criterion.name,
            "criterion_description": criterion.description,
            "max_score": criterion.max_score,
            "scoring_levels": levels,
            "evidence_required": ", ".join(criterion.evidence_required),
            "human_review_triggers": ", ".join(criterion.human_review_triggers),
            "protected_attribute_note": protected_note,
            "evidence_text": context.evidence_text or "No evidence available.",
        }
    )


def replace_persisted_scores(
    *,
    db: Session,
    application_id: str,
    outcome: RubricScoringOutcome,
    rubric: AdmissionsRubric,
) -> tuple[list[RubricCriterionScore], RubricScorecard]:
    db.execute(delete(RubricCriterionScore).where(RubricCriterionScore.application_id == application_id))
    existing_scorecard = db.get(RubricScorecard, application_id)
    if existing_scorecard is not None:
        db.delete(existing_scorecard)
        db.flush()

    weights = {criterion.criterion_id: criterion.weight for criterion in rubric.criteria}
    score_rows = [
        RubricCriterionScore(
            criterion_id=result.criterion_id,
            application_id=application_id,
            criterion_name=result.criterion_name,
            score=result.score,
            max_score=result.max_score,
            weight=weights[result.criterion_id],
            rationale=result.rationale,
            supporting_evidence=[item.model_dump(mode="json") for item in result.supporting_evidence],
            missing_information=result.missing_information,
            confidence=result.confidence,
            requires_human_review=result.requires_human_review,
            risk_flags=result.risk_flags,
            scoring_metadata={
                **load_prompt("rubric_scoring").identity(),
                "rubric_id": rubric.rubric_id,
                "rubric_name": rubric.name,
            },
        )
        for result in outcome.criteria
    ]
    scorecard = RubricScorecard(
        application_id=application_id,
        weighted_score=outcome.weighted_score,
        max_score=outcome.max_score,
        recommendation_band=outcome.recommendation_band,
        decision_support_summary=outcome.decision_support_summary,
        reviewer_override=None,
        requires_human_review=outcome.requires_human_review,
        risk_flags=outcome.risk_flags,
    )
    db.add_all(score_rows)
    db.add(scorecard)
    db.commit()
    for row in score_rows:
        db.refresh(row)
    db.refresh(scorecard)
    return score_rows, scorecard
