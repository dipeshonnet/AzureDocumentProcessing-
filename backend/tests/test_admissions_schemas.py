from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas.admissions import (
    ApplicantCreate,
    ApplicationDocumentCreate,
    DocumentSummaryCreate,
    RubricCriterionScoreCreate,
    RubricScorecardCreate,
)


def test_applicant_schema_accepts_valid_payload() -> None:
    applicant = ApplicantCreate(
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.edu",
        program_applied="MSc Computer Science",
        intake_term="Fall 2026",
    )

    assert applicant.status == "draft"
    assert applicant.email == "ada@example.edu"


def test_applicant_schema_rejects_invalid_email() -> None:
    with pytest.raises(ValidationError):
        ApplicantCreate(
            first_name="Ada",
            last_name="Lovelace",
            email="not-an-email",
            program_applied="MSc Computer Science",
            intake_term="Fall 2026",
        )


def test_document_schema_bounds_classification_confidence() -> None:
    with pytest.raises(ValidationError):
        ApplicationDocumentCreate(
            application_id="application-1",
            original_filename="transcript.pdf",
            blob_url_or_path="local/transcript.pdf",
            classification_confidence=1.2,
        )


def test_summary_schema_uses_independent_json_defaults() -> None:
    first = DocumentSummaryCreate(document_id="document-1")
    second = DocumentSummaryCreate(document_id="document-2")

    first.strengths.append("Strong grades")

    assert second.strengths == []


def test_rubric_score_rejects_score_above_max_score() -> None:
    with pytest.raises(ValidationError):
        RubricCriterionScoreCreate(
            application_id="application-1",
            criterion_name="Academic preparation",
            score=6,
            max_score=5,
            confidence=0.7,
        )


def test_scorecard_schema_accepts_decision_support_payload() -> None:
    scorecard = RubricScorecardCreate(
        application_id="application-1",
        weighted_score=82.5,
        max_score=100,
        recommendation_band="review_recommended",
        decision_support_summary="Evidence appears complete enough for human review.",
        reviewer_override={
            "overridden_by": "reviewer-1",
            "overridden_at": datetime.now(timezone.utc).isoformat(),
            "reason": "Reviewer adjusted evidence weighting.",
        },
    )

    assert scorecard.recommendation_band == "review_recommended"
    assert scorecard.reviewer_override is not None
