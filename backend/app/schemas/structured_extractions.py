from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceSnippet(StrictSchema):
    snippet: NonEmptyString
    page_number: int | None = Field(..., ge=1)
    source: str | None = Field(...)


class TextField(StrictSchema):
    value: str | None = Field(...)
    evidence: list[EvidenceSnippet] = Field(...)
    uncertain: bool = Field(...)
    explanation: str | None = Field(...)

    @model_validator(mode="after")
    def require_value_or_uncertainty(self) -> TextField:
        has_value = bool((self.value or "").strip())
        _validate_evidence_policy(
            has_value=has_value,
            has_evidence=bool(self.evidence),
            uncertain=self.uncertain,
            explanation=self.explanation,
        )
        return self


class TextListField(StrictSchema):
    value: list[str] = Field(...)
    evidence: list[EvidenceSnippet] = Field(...)
    uncertain: bool = Field(...)
    explanation: str | None = Field(...)

    @model_validator(mode="after")
    def require_values_or_uncertainty(self) -> TextListField:
        has_value = any(item.strip() for item in self.value)
        _validate_evidence_policy(
            has_value=has_value,
            has_evidence=bool(self.evidence),
            uncertain=self.uncertain,
            explanation=self.explanation,
        )
        return self


class NumberField(StrictSchema):
    value: float | None = Field(...)
    evidence: list[EvidenceSnippet] = Field(...)
    uncertain: bool = Field(...)
    explanation: str | None = Field(...)

    @model_validator(mode="after")
    def require_non_negative_value_or_uncertainty(self) -> NumberField:
        if self.value is not None and self.value < 0:
            raise ValueError("numeric extracted values cannot be negative")
        _validate_evidence_policy(
            has_value=self.value is not None,
            has_evidence=bool(self.evidence),
            uncertain=self.uncertain,
            explanation=self.explanation,
        )
        return self


class ScoreComponent(StrictSchema):
    name: NonEmptyString
    score: float = Field(ge=0)
    evidence: list[EvidenceSnippet] = Field(...)
    uncertain: bool = Field(...)
    explanation: str | None = Field(...)

    @model_validator(mode="after")
    def require_component_evidence_or_uncertainty(self) -> ScoreComponent:
        _validate_evidence_policy(
            has_value=True,
            has_evidence=bool(self.evidence),
            uncertain=self.uncertain,
            explanation=self.explanation,
        )
        return self


class ScoreComponentsField(StrictSchema):
    value: list[ScoreComponent] = Field(...)
    evidence: list[EvidenceSnippet] = Field(...)
    uncertain: bool = Field(...)
    explanation: str | None = Field(...)

    @model_validator(mode="after")
    def require_components_or_uncertainty(self) -> ScoreComponentsField:
        _validate_evidence_policy(
            has_value=bool(self.value),
            has_evidence=bool(self.evidence),
            uncertain=self.uncertain,
            explanation=self.explanation,
        )
        return self


class TranscriptExtraction(StrictSchema):
    institution: TextField
    degree: TextField
    major: TextField
    gpa: NumberField
    gpa_scale: NumberField
    coursework_highlights: TextListField
    academic_honors: TextListField
    academic_risks: TextListField
    evidence: list[EvidenceSnippet]

    @model_validator(mode="after")
    def validate_gpa_scale(self) -> TranscriptExtraction:
        if self.gpa.value is not None and self.gpa_scale.value is not None:
            if self.gpa.value > self.gpa_scale.value:
                raise ValueError("gpa cannot be greater than gpa_scale")
        return self


class PersonalStatementExtraction(StrictSchema):
    applicant_goals: TextListField
    motivation: TextField
    program_fit: TextField
    academic_interests: TextListField
    career_goals: TextField
    writing_quality_observations: TextListField
    notable_strengths: TextListField
    concerns_or_gaps: TextListField
    evidence: list[EvidenceSnippet]


class RecommendationLetterExtraction(StrictSchema):
    recommender_name: TextField
    recommender_role: TextField
    relationship_to_applicant: TextField
    recommendation_strength: TextField
    academic_ability: TextField
    character_traits: TextListField
    leadership_or_teamwork: TextField
    concerns_or_caveats: TextListField
    evidence: list[EvidenceSnippet]

    @model_validator(mode="after")
    def validate_recommendation_strength(self) -> RecommendationLetterExtraction:
        value = (self.recommendation_strength.value or "").strip()
        explanation = (self.recommendation_strength.explanation or "").strip()
        if not value and not explanation:
            raise ValueError("empty recommendation_strength requires an explanation")
        return self


class ResumeExtraction(StrictSchema):
    education: TextListField
    work_experience: TextListField
    projects: TextListField
    leadership: TextListField
    awards: TextListField
    skills: TextListField
    evidence: list[EvidenceSnippet]


class TestScoreExtraction(StrictSchema):
    test_name: TextField
    score: NumberField
    score_components: ScoreComponentsField
    test_date: TextField
    evidence: list[EvidenceSnippet]


STRUCTURED_EXTRACTION_SCHEMAS: dict[str, type[StrictSchema]] = {
    "transcript": TranscriptExtraction,
    "personal_statement": PersonalStatementExtraction,
    "recommendation_letter": RecommendationLetterExtraction,
    "resume_cv": ResumeExtraction,
    "test_score_report": TestScoreExtraction,
}


def _validate_evidence_policy(
    *,
    has_value: bool,
    has_evidence: bool,
    uncertain: bool,
    explanation: str | None,
) -> None:
    if has_value and has_evidence:
        return
    if uncertain and (explanation or has_evidence):
        return
    if not has_value:
        raise ValueError("missing extracted value must be marked uncertain with an explanation")
    raise ValueError("extracted values require evidence or an uncertainty explanation")
