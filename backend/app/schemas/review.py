from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.schemas.admissions import (
    ApplicantRead,
    ApplicationDocumentRead,
    ApplicationRead,
    DocumentSummaryRead,
    ExtractedDocumentContentRead,
    RubricCriterionScoreRead,
    RubricScorecardRead,
)


NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
FinalHumanDecision = Literal["admit", "waitlist", "reject", "needs_more_information", "defer"]


class ReviewSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ReviewerApplicationListItem(ReviewSchema):
    application_id: str
    applicant: ApplicantRead
    submitted_at: datetime | None = None
    processing_status: str
    review_status: str
    final_human_decision: str | None = None
    weighted_score: float | None = None
    max_score: float | None = None
    recommendation_band: str | None = None
    requires_human_review: bool
    document_count: int


class ReviewerDocumentDetail(ReviewSchema):
    document: ApplicationDocumentRead
    classification: dict[str, Any]
    extraction: ExtractedDocumentContentRead | None = None
    structured_extracted_fields: dict[str, Any] = Field(default_factory=dict)
    summary: DocumentSummaryRead | None = None
    model_versions: dict[str, Any] = Field(default_factory=dict)
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    human_review_flags: list[str] = Field(default_factory=list)


class ReviewerApplicationDetail(ReviewSchema):
    application: ApplicationRead
    applicant: ApplicantRead
    documents: list[ReviewerDocumentDetail]
    rubric_criterion_scores: list[RubricCriterionScoreRead]
    supporting_evidence: list[dict[str, Any]] = Field(default_factory=list)
    confidence: dict[str, Any] = Field(default_factory=dict)
    human_review_flags: list[str] = Field(default_factory=list)
    weighted_score: float | None = None
    max_score: float | None = None
    scorecard: RubricScorecardRead | None = None
    model_versions: dict[str, Any] = Field(default_factory=dict)
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    reviewer_overrides: dict[str, Any] = Field(default_factory=dict)
    final_human_decision: str | None = None
    audit_metadata: list[dict[str, Any]] = Field(default_factory=list)
    ai_recommendation_is_final_decision: bool = False
    decision_support_only: bool = True


class CriterionOverrideRequest(ReviewSchema):
    reason: NonEmptyString
    reviewer_id: str | None = None
    score: float | None = Field(default=None, ge=0)
    rationale: str | None = None
    requires_human_review: bool | None = None


class CriterionOverrideResponse(ReviewSchema):
    application_id: str
    criterion_id: str
    reviewer_override: dict[str, Any]
    audit_metadata: list[dict[str, Any]]


class FinalDecisionRequest(ReviewSchema):
    decision: FinalHumanDecision
    reviewer_id: str | None = None
    reason: str | None = None
    ai_recommendation_acknowledged: bool = True

    @model_validator(mode="after")
    def require_ai_acknowledgement(self) -> FinalDecisionRequest:
        if not self.ai_recommendation_acknowledged:
            raise ValueError("reviewer must acknowledge that AI recommendation is not final")
        return self


class FinalDecisionResponse(ReviewSchema):
    application_id: str
    final_human_decision: FinalHumanDecision
    audit_metadata: list[dict[str, Any]]
    ai_recommendation_is_final_decision: bool = False


class ReviewerNotesRequest(ReviewSchema):
    notes: NonEmptyString
    reviewer_id: str | None = None


class ReviewerNotesResponse(ReviewSchema):
    application_id: str
    reviewer_notes: str
    audit_metadata: list[dict[str, Any]]
