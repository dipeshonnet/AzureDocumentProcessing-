from __future__ import annotations

import re
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SavedRubric
from app.schemas.intake import SavedRubricCreate, SavedRubricRead, SavedRubricUpdate


DEFAULT_OPERATIONS_RUBRIC_ID = "default_admissions_rubric"


def ensure_default_saved_rubrics(db: Session) -> None:
    existing_count = db.scalar(select(SavedRubric).limit(1))
    if existing_count is not None:
        return
    payload = default_operations_rubric_payload()
    db.add(
        SavedRubric(
            rubric_id=payload.rubric_id or DEFAULT_OPERATIONS_RUBRIC_ID,
            name=payload.name,
            description=payload.description,
            total_points=payload.total_points,
            sections=[section.model_dump() for section in payload.sections],
            version=1,
            is_active=True,
        )
    )
    db.commit()


def list_saved_rubrics(db: Session) -> list[SavedRubric]:
    ensure_default_saved_rubrics(db)
    return list(db.scalars(select(SavedRubric).order_by(SavedRubric.updated_at.desc(), SavedRubric.name)))


def get_saved_rubric(db: Session, rubric_id: str) -> SavedRubric | None:
    ensure_default_saved_rubrics(db)
    return db.get(SavedRubric, rubric_id)


def create_saved_rubric(db: Session, payload: SavedRubricCreate) -> SavedRubric:
    rubric_id = payload.rubric_id.strip() if payload.rubric_id else slugify(payload.name)
    if not rubric_id:
        rubric_id = f"rubric_{uuid4().hex[:8]}"
    base_id = rubric_id[:140]
    candidate = base_id
    suffix = 2
    while db.get(SavedRubric, candidate) is not None:
        candidate = f"{base_id}_{suffix}"
        suffix += 1
    rubric = SavedRubric(
        rubric_id=candidate,
        name=payload.name,
        description=payload.description,
        total_points=payload.total_points,
        sections=[section.model_dump() for section in payload.sections],
        version=1,
        is_active=True,
    )
    db.add(rubric)
    db.commit()
    db.refresh(rubric)
    return rubric


def update_saved_rubric(db: Session, rubric: SavedRubric, payload: SavedRubricUpdate) -> SavedRubric:
    rubric.name = payload.name
    rubric.description = payload.description
    rubric.total_points = payload.total_points
    rubric.sections = [section.model_dump() for section in payload.sections]
    rubric.version = payload.version
    rubric.is_active = payload.is_active
    rubric.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(rubric)
    return rubric


def saved_rubric_to_read(rubric: SavedRubric) -> SavedRubricRead:
    return SavedRubricRead(
        rubric_id=rubric.rubric_id,
        name=rubric.name,
        description=rubric.description,
        total_points=rubric.total_points,
        sections=rubric.sections,
        version=rubric.version,
        is_active=rubric.is_active,
        created_at=rubric.created_at,
        updated_at=rubric.updated_at,
    )


def slugify(value: str) -> str:
    lowered = value.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")
    return slug[:140]


def default_operations_rubric_payload() -> SavedRubricCreate:
    return SavedRubricCreate.model_validate(
        {
            "rubric_id": DEFAULT_OPERATIONS_RUBRIC_ID,
            "name": "Nursing admissions operations rubric",
            "description": "Editable operational rubric template based on GPA, prerequisites, recommendation letters, short answer, experience, and socioeconomic context. Decision support only.",
            "total_points": 100,
            "sections": [
                {
                    "section_id": "gpa",
                    "name": "GPA",
                    "description": "Academic GPA review. Example guideline shows Science GPA scoring tiers.",
                    "max_points": 30,
                    "components": [
                        {
                            "component_id": "science_gpa",
                            "name": "Science GPA",
                            "description": "Use institution-approved transcript evidence.",
                            "max_points": 20,
                            "rows": [
                                {"row_id": "science_gpa_375_400", "label": "Science GPA", "condition": "3.75-4.0", "points": "20"},
                                {"row_id": "science_gpa_350_374", "label": "Science GPA", "condition": "3.5-3.74", "points": "18"},
                                {"row_id": "science_gpa_326_349", "label": "Science GPA", "condition": "3.26-3.49", "points": "12"},
                                {"row_id": "science_gpa_300_325", "label": "Science GPA", "condition": "3.0-3.25", "points": "6"},
                                {"row_id": "science_gpa_below_300", "label": "Science GPA", "condition": "<3.0", "points": "0"},
                            ],
                        }
                    ],
                },
                {
                    "section_id": "prerequisites",
                    "name": "Prerequisites",
                    "description": "Prerequisite status review.",
                    "max_points": 5,
                    "components": [
                        {
                            "component_id": "prerequisite_status",
                            "name": "Prerequisite Status",
                            "description": "Interpret Acceptable / Not Acceptable as staff-facing guidance.",
                            "max_points": 5,
                            "rows": [
                                {"row_id": "prereq_0_3", "label": "Prerequisites IP/Planned", "condition": "0-3", "points": "Acceptable"},
                                {"row_id": "prereq_4_more", "label": "Prerequisites IP/Planned", "condition": "4 or more", "points": "Not acceptable"},
                                {"row_id": "science_prereq_0_1", "label": "Science Prerequisites IP/Planned", "condition": "0-1", "points": "Acceptable"},
                                {"row_id": "science_prereq_2_more", "label": "Science Prerequisites IP/Planned", "condition": "2 or more", "points": "Not Acceptable"},
                            ],
                        }
                    ],
                },
                {
                    "section_id": "recommendation",
                    "name": "Letters of recommendation",
                    "description": "Score two letters only. Max score is 10 points for each letter and 20 points for two letters combined.",
                    "max_points": 20,
                    "components": [
                        {
                            "component_id": "reference_type",
                            "name": "Reference Type",
                            "description": "One faculty preferably math/science instructor, and one healthcare professional. Family/friends are not considered.",
                            "max_points": 5,
                            "rows": [
                                {"row_id": "reference_healthcare", "label": "Healthcare professional/other professional", "condition": "", "points": "5"},
                                {"row_id": "reference_instructor", "label": "Instructor science/math", "condition": "", "points": "5"},
                                {"row_id": "reference_family_friend", "label": "Family or Friend", "condition": "", "points": "0"},
                            ],
                        },
                        {
                            "component_id": "lor_overall_evaluation",
                            "name": "LOR Overall Evaluation",
                            "description": "Overall strength for each letter.",
                            "max_points": 5,
                            "rows": [
                                {"row_id": "lor_excellent", "label": "Excellent (5)", "condition": "", "points": "5"},
                                {"row_id": "lor_good", "label": "Good (4)", "condition": "", "points": "3"},
                                {"row_id": "lor_average", "label": "Average (3)", "condition": "", "points": "2"},
                                {"row_id": "lor_below_average", "label": "Below Average (2) or Poor (1)", "condition": "", "points": "0"},
                            ],
                        },
                    ],
                },
                {
                    "section_id": "short_answer",
                    "name": "Short answer",
                    "description": "Writing and relevance review for applicant response.",
                    "max_points": 20,
                    "components": [
                        {
                            "component_id": "short_answer_rating",
                            "name": "Rubric score conversion",
                            "description": "Convert rubric score to section points.",
                            "max_points": 20,
                            "rows": [
                                {"row_id": "short_answer_excellent", "label": "Excellent", "condition": "Rubric score 6", "points": "20"},
                                {"row_id": "short_answer_adequate", "label": "Adequate", "condition": "Rubric score 3-5", "points": "10"},
                                {"row_id": "short_answer_unacceptable", "label": "Unacceptable", "condition": "Rubric score 0-2", "points": "5"},
                            ],
                        },
                        {
                            "component_id": "writing_relevance",
                            "name": "Writing and Relevance",
                            "description": "Short Answer Response Rubric.",
                            "max_points": 3,
                            "rows": [
                                {"row_id": "writing_3", "label": "Writing and Relevance", "condition": "3", "points": "3", "notes": "Excellent, clear statement of goals and explicit understanding of nursing and program fit."},
                                {"row_id": "writing_2", "label": "Writing and Relevance", "condition": "2", "points": "2", "notes": "Strong, with several goals and/or connection to nursing and some leadership or work experience."},
                                {"row_id": "writing_1", "label": "Writing and Relevance", "condition": "1", "points": "1", "notes": "Weak, but with some goals and/or connection to nursing."},
                                {"row_id": "writing_0", "label": "Writing and Relevance", "condition": "0", "points": "0", "notes": "Unclear intent, poor writing quality, no clear understanding."},
                            ],
                        },
                    ],
                },
                {
                    "section_id": "experience",
                    "name": "Experience",
                    "description": "Relevant leadership, volunteer, work, or healthcare exposure.",
                    "max_points": 10,
                    "components": [
                        {
                            "component_id": "experience_review",
                            "name": "Experience review",
                            "description": "Customize rows to match the program policy.",
                            "max_points": 10,
                            "rows": [
                                {"row_id": "experience_strong", "label": "Strong", "condition": "Sustained relevant experience", "points": "10"},
                                {"row_id": "experience_some", "label": "Some", "condition": "Some relevant experience", "points": "5"},
                                {"row_id": "experience_limited", "label": "Limited", "condition": "Limited evidence", "points": "0"},
                            ],
                        }
                    ],
                },
                {
                    "section_id": "socioeconomic_status",
                    "name": "Socioeconomic status",
                    "description": "Contextual review only where institution policy allows. Do not use protected traits as scoring factors.",
                    "max_points": 15,
                    "components": [
                        {
                            "component_id": "context_review",
                            "name": "Contextual review",
                            "description": "Human reviewer verifies approved contextual factors.",
                            "max_points": 15,
                            "rows": [
                                {"row_id": "context_high", "label": "High approved context", "condition": "Verified policy-aligned evidence", "points": "15"},
                                {"row_id": "context_moderate", "label": "Moderate approved context", "condition": "Some policy-aligned evidence", "points": "8"},
                                {"row_id": "context_none", "label": "No approved context", "condition": "No eligible evidence", "points": "0"},
                            ],
                        }
                    ],
                },
            ],
        }
    )
