from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


EmailString = Annotated[
    str,
    StringConstraints(
        min_length=3,
        max_length=320,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    ),
]
NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
IdentifierString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
ConfidenceScore = Annotated[float, Field(ge=0, le=1)]


class AdmissionsSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ApplicantBase(AdmissionsSchema):
    student_unique_id: str | None = Field(default=None, max_length=120)
    first_name: NonEmptyString
    last_name: NonEmptyString
    email: EmailString
    program_applied: NonEmptyString
    intake_term: NonEmptyString
    status: IdentifierString = "draft"


class ApplicantCreate(ApplicantBase):
    pass


class ApplicantRead(ApplicantBase):
    applicant_id: str


class ApplicationBase(AdmissionsSchema):
    applicant_id: str
    submitted_at: datetime | None = None
    program_applied: str | None = None
    intake_term: str | None = None
    processing_status: IdentifierString = "pending"
    review_status: IdentifierString = "pending"
    final_human_decision: str | None = None
    reviewer_notes: str | None = None
    processing_errors: list[dict[str, Any]] = Field(default_factory=list)


class ApplicationCreate(ApplicationBase):
    pass


class ApplicationRead(ApplicationBase):
    application_id: str


class ApplicationDocumentBase(AdmissionsSchema):
    application_id: str
    original_filename: NonEmptyString
    blob_url_or_path: NonEmptyString
    document_type: str | None = None
    classification_confidence: ConfidenceScore | None = None
    classification_metadata: dict[str, Any] = Field(default_factory=dict)
    processing_status: IdentifierString = "uploaded"
    page_count: int | None = Field(default=None, ge=0)


class ApplicationDocumentCreate(ApplicationDocumentBase):
    pass


class ApplicationDocumentRead(ApplicationDocumentBase):
    document_id: str
    created_at: datetime


class DocumentClassificationRead(AdmissionsSchema):
    document_id: str
    document_type: str
    confidence: ConfidenceScore
    rationale: str
    evidence_snippets: list[str] = Field(default_factory=list)
    requires_human_review: bool
    source: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExtractedDocumentContentBase(AdmissionsSchema):
    document_id: str
    raw_text: str | None = None
    pages: list[dict[str, Any]] = Field(default_factory=list)
    tables: list[dict[str, Any]] = Field(default_factory=list)
    key_value_pairs: dict[str, Any] = Field(default_factory=dict)
    extraction_confidence: ConfidenceScore | None = None
    extraction_metadata: dict[str, Any] = Field(default_factory=dict)
    structured_extraction: dict[str, Any] = Field(default_factory=dict)


class ExtractedDocumentContentCreate(ExtractedDocumentContentBase):
    pass


class ExtractedDocumentContentRead(ExtractedDocumentContentBase):
    pass


class StructuredExtractionRead(AdmissionsSchema):
    document_id: str
    document_type: str
    extraction: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentSummaryBase(AdmissionsSchema):
    document_id: str
    short_summary: str = ""
    section_summaries: list[dict[str, Any]] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    reviewer_attention_points: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    summary_metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentSummaryCreate(DocumentSummaryBase):
    pass


class DocumentSummaryRead(DocumentSummaryBase):
    pass


class RubricCriterionScoreBase(AdmissionsSchema):
    application_id: str
    criterion_name: NonEmptyString
    criterion_id: str | None = None
    score: float = Field(ge=0)
    max_score: float = Field(gt=0)
    weight: float = Field(default=1.0, ge=0)
    rationale: str | None = None
    supporting_evidence: list[dict[str, Any]] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    confidence: ConfidenceScore | None = None
    requires_human_review: bool = True
    risk_flags: list[str] = Field(default_factory=list)
    reviewer_override: dict[str, Any] | None = None
    scoring_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def score_must_not_exceed_max(self) -> RubricCriterionScoreBase:
        if self.score > self.max_score:
            raise ValueError("score must not exceed max_score")
        return self


class RubricCriterionScoreCreate(RubricCriterionScoreBase):
    pass


class RubricCriterionScoreRead(RubricCriterionScoreBase):
    score_id: str
    criterion_id: str


class RubricScorecardBase(AdmissionsSchema):
    application_id: str
    weighted_score: float = Field(ge=0)
    max_score: float = Field(gt=0)
    recommendation_band: IdentifierString
    decision_support_summary: str | None = None
    reviewer_override: dict[str, Any] | None = None
    requires_human_review: bool = True
    risk_flags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def weighted_score_must_not_exceed_max(self) -> RubricScorecardBase:
        if self.weighted_score > self.max_score:
            raise ValueError("weighted_score must not exceed max_score")
        return self


class RubricScorecardCreate(RubricScorecardBase):
    pass


class RubricScorecardRead(RubricScorecardBase):
    created_at: datetime


class ApplicationRubricScoreRead(AdmissionsSchema):
    application_id: str
    weighted_score: float
    max_score: float
    recommendation_band: str
    decision_support_summary: str
    requires_human_review: bool
    risk_flags: list[str] = Field(default_factory=list)
    criteria: list[RubricCriterionScoreRead]
    scorecard: RubricScorecardRead


class ApplicationProcessingStatusRead(AdmissionsSchema):
    application_id: str
    processing_status: str
    review_status: str
    processing_errors: list[dict[str, Any]] = Field(default_factory=list)
    documents: list[ApplicationDocumentRead] = Field(default_factory=list)
    scorecard: RubricScorecardRead | None = None


class ApplicationProcessingResultRead(ApplicationProcessingStatusRead):
    scorecard_detail: ApplicationRubricScoreRead | None = None
