"""Pydantic schemas for API requests and responses."""

from app.schemas.admissions import (
    ApplicantCreate,
    ApplicantRead,
    ApplicationCreate,
    ApplicationDocumentCreate,
    ApplicationDocumentRead,
    ApplicationRead,
    ApplicationRubricScoreRead,
    DocumentClassificationRead,
    DocumentSummaryCreate,
    DocumentSummaryRead,
    ExtractedDocumentContentCreate,
    ExtractedDocumentContentRead,
    RubricCriterionScoreCreate,
    RubricCriterionScoreRead,
    RubricScorecardCreate,
    RubricScorecardRead,
    StructuredExtractionRead,
)
from app.schemas.rubrics import AdmissionsRubric, RubricCriterion

__all__ = [
    "ApplicantCreate",
    "ApplicantRead",
    "ApplicationCreate",
    "ApplicationDocumentCreate",
    "ApplicationDocumentRead",
    "ApplicationRead",
    "ApplicationRubricScoreRead",
    "DocumentClassificationRead",
    "DocumentSummaryCreate",
    "DocumentSummaryRead",
    "ExtractedDocumentContentCreate",
    "ExtractedDocumentContentRead",
    "RubricCriterionScoreCreate",
    "RubricCriterionScoreRead",
    "RubricScorecardCreate",
    "RubricScorecardRead",
    "StructuredExtractionRead",
    "AdmissionsRubric",
    "RubricCriterion",
]
