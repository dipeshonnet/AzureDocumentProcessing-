from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppSettings, get_settings
from app.db.dependencies import get_db
from app.db.init_db import init_db
from app.db.session import create_db_engine, create_session_factory
from app.main import create_app
from app.models import (
    Applicant,
    ApplicationDocument,
    DocumentSummary,
    ExtractedDocumentContent,
    IntakeJob,
    RubricCriterionScore,
    RubricScorecard,
    SavedRubric,
)
from app.services.rubric_scoring import (
    MockRubricScoringService,
    RubricCriterionEvidence,
    RubricCriterionScoreResult,
    RubricScoringContext,
    build_rubric_scoring_prompt,
    calculate_scorecard,
    saved_rubric_to_admissions_rubric,
)
from app.services.rubrics import load_default_rubric


@pytest.fixture
def scoring_client(
    tmp_path: Path,
) -> tuple[TestClient, sessionmaker[Session]]:
    database_path = tmp_path / "admissions-scoring-test.db"
    engine = create_db_engine(f"sqlite:///{database_path}")
    init_db(engine)
    session_factory = create_session_factory(engine)

    settings = AppSettings.from_mapping(
        {
            "APP_ENV": "test",
            "DATABASE_URL": f"sqlite:///{database_path}",
            "RUBRIC_SCORING_BACKEND": "mock",
            "RUBRIC_SCORING_TEXT_MAX_CHARS": "4000",
            "RUBRIC_SCORING_LOW_CONFIDENCE_THRESHOLD": "0.65",
        }
    )

    def override_db() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db] = override_db
    return TestClient(app), session_factory


def create_application(client: TestClient, session_factory: sessionmaker[Session]) -> str:
    with session_factory() as session:
        applicant = Applicant(
            first_name="Valerie",
            last_name="Thomas",
            email="valerie@example.edu",
            program_applied="MSc Data Science",
            intake_term="Fall 2026",
            status="submitted",
        )
        session.add(applicant)
        session.commit()
        session.refresh(applicant)
        applicant_id = applicant.applicant_id

    response = client.post("/api/v1/applications", json={"applicant_id": applicant_id})
    assert response.status_code == 201
    return response.json()["application_id"]


def add_evidence_document(
    session_factory: sessionmaker[Session],
    *,
    application_id: str,
    raw_text: str = "Transcript shows advanced coursework and strong preparation.",
    confidence: float = 0.9,
) -> str:
    with session_factory() as session:
        document = ApplicationDocument(
            application_id=application_id,
            original_filename="transcript.pdf",
            blob_url_or_path="local/transcript.pdf",
            document_type="transcript",
            classification_confidence=0.95,
            classification_metadata={"source": "test"},
            processing_status="summarized",
            page_count=1,
        )
        session.add(document)
        session.flush()
        session.add(
            ExtractedDocumentContent(
                document_id=document.document_id,
                raw_text=raw_text,
                pages=[{"page_number": 1, "text": raw_text, "lines": [], "words": []}],
                tables=[],
                key_value_pairs={"values": {}, "pairs": []},
                extraction_confidence=confidence,
                extraction_metadata={"provider": "test"},
                structured_extraction={
                    "document_type": "transcript",
                    "extraction": {"academic_readiness": raw_text},
                    "metadata": {"source": "test"},
                },
            )
        )
        session.add(
            DocumentSummary(
                document_id=document.document_id,
                short_summary="The document contains academic evidence.",
                section_summaries=[
                    {
                        "section_title": "Academic content",
                        "summary": "Advanced coursework is visible.",
                        "evidence": [{"snippet": raw_text, "page_number": 1, "source": "test"}],
                    }
                ],
                strengths=["Advanced coursework"],
                concerns=[],
                missing_information=[],
                reviewer_attention_points=["Verify transcript manually."],
                evidence=[{"snippet": raw_text, "page_number": 1, "source": "test"}],
            )
        )
        session.commit()
        return document.document_id


def test_score_application_uses_all_default_criteria(
    scoring_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = scoring_client
    application_id = create_application(client, session_factory)
    add_evidence_document(session_factory, application_id=application_id)

    response = client.post(f"/api/v1/applications/{application_id}/score")

    assert response.status_code == 200
    payload = response.json()
    criterion_ids = {criterion["criterion_id"] for criterion in payload["criteria"]}
    assert criterion_ids == {
        "academic_readiness",
        "writing_quality",
        "program_fit",
        "recommendation_strength",
        "leadership_and_experience",
        "risk_and_missing_information",
    }
    assert len(payload["criteria"]) == 6

    with session_factory() as session:
        persisted_scores = session.query(RubricCriterionScore).filter_by(application_id=application_id).all()
        persisted_scorecard = session.get(RubricScorecard, application_id)
        assert len(persisted_scores) == 6
        assert persisted_scorecard is not None


def test_saved_rubric_sections_drive_scoring_and_document_alignment(
    scoring_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = scoring_client
    application_id = create_application(client, session_factory)
    document_id = add_evidence_document(
        session_factory,
        application_id=application_id,
        raw_text="Applicant connects legal goals to prior debate and investment management experience.",
    )
    with session_factory() as session:
        document = session.get(ApplicationDocument, document_id)
        assert document is not None
        document.document_type = "personal_statement"
        document.original_filename = "03_Personal_Statement.pdf"
        session.add(
            SavedRubric(
                rubric_id="law_personal_statement_rubric",
                name="Law admissions rubric",
                description="Law program rubric.",
                total_points=40,
                sections=[
                    {
                        "section_id": "personal_statement",
                        "name": "Personal Statement",
                        "description": "Evaluate the applicant-authored personal statement.",
                        "max_points": 20,
                        "components": [
                            {
                                "component_id": "goals",
                                "name": "Goals and Fit",
                                "description": "Connection between goals and law school.",
                                "max_points": 20,
                                "rows": [
                                    {
                                        "row_id": "excellent",
                                        "label": "Excellent",
                                        "condition": "Specific legal goals with evidence",
                                        "points": "20",
                                        "notes": "Strong fit",
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "section_id": "short_answer",
                        "name": "Short answer",
                        "description": "Evaluate short-answer responses only.",
                        "max_points": 20,
                        "components": [],
                    },
                ],
                version=1,
                is_active=True,
            )
        )
        session.add(
            IntakeJob(
                application_id=application_id,
                document_id=document_id,
                rubric_id="law_personal_statement_rubric",
                status="completed",
                parser_mode="azure",
                status_message="Done",
            )
        )
        session.commit()

    with session_factory() as session:
        service = MockRubricScoringService(
            db=session,
            settings=AppSettings.from_mapping({"APP_ENV": "test", "RUBRIC_SCORING_BACKEND": "mock"}),
        )
        outcome = service.score_application(application_id)
        by_id = {criterion.criterion_id: criterion for criterion in outcome.criteria}
        document = session.get(ApplicationDocument, document_id)

        assert set(by_id) == {"personal_statement", "short_answer"}
        assert by_id["personal_statement"].score == 3.0
        assert by_id["personal_statement"].supporting_evidence
        assert by_id["short_answer"].score == 1.0
        assert by_id["short_answer"].supporting_evidence == []
        assert document is not None
        assert document.document_type == "personal_statement"
        assert document.classification_metadata["rubric_section_matches"][0]["rubric_section_id"] == "personal_statement"


def test_saved_rubric_rows_are_included_in_scoring_prompt() -> None:
    saved_rubric = SavedRubric(
        rubric_id="law_personal_statement_rubric",
        name="Law admissions rubric",
        description="Law program rubric.",
        total_points=20,
        sections=[
            {
                "section_id": "personal_statement",
                "name": "Personal Statement",
                "description": "Evaluate personal statement quality.",
                "max_points": 20,
                "components": [
                    {
                        "component_id": "goals",
                        "name": "Goals and Fit",
                        "description": "Connection between goals and law school.",
                        "max_points": 20,
                        "rows": [
                            {
                                "row_id": "excellent",
                                "label": "Excellent",
                                "condition": "Specific legal goals with evidence",
                                "points": "20",
                                "notes": "Strong fit",
                            }
                        ],
                    }
                ],
            }
        ],
        version=1,
        is_active=True,
    )
    rubric = saved_rubric_to_admissions_rubric(saved_rubric)
    prompt = build_rubric_scoring_prompt(
        RubricScoringContext(
            application_id="app-1",
            criterion=rubric.criteria[0],
            rubric=rubric,
            evidence_text="Applicant evidence.",
            protected_attribute_text_present=False,
        )
    )

    assert "Component: Goals and Fit" in prompt
    assert "condition=Specific legal goals with evidence" in prompt
    assert "points=20" in prompt


def test_weighted_score_is_calculated_in_code() -> None:
    rubric = load_default_rubric()
    scores = [
        RubricCriterionScoreResult(
            criterion_id=criterion.criterion_id,
            criterion_name=criterion.name,
            score=criterion.max_score if criterion.criterion_id == "academic_readiness" else 0,
            max_score=criterion.max_score,
            rationale="Synthetic test score.",
            supporting_evidence=[
                RubricCriterionEvidence(
                    snippet="Evidence",
                    document_id=None,
                    page_number=1,
                    source="test",
                )
            ],
            missing_information=[],
            confidence=0.9,
            requires_human_review=False,
            risk_flags=[],
        )
        for criterion in rubric.criteria
    ]

    outcome = calculate_scorecard(results=scores, rubric=rubric)

    assert outcome.weighted_score == pytest.approx(1.25)
    assert outcome.max_score == pytest.approx(5.0)
    assert outcome.recommendation_band == "limited_evidence_support"


def test_low_confidence_triggers_human_review(tmp_path: Path) -> None:
    database_path = tmp_path / "low-confidence.db"
    engine = create_db_engine(f"sqlite:///{database_path}")
    init_db(engine)
    session_factory = create_session_factory(engine)
    settings = AppSettings.from_mapping(
        {
            "APP_ENV": "test",
            "DATABASE_URL": f"sqlite:///{database_path}",
            "RUBRIC_SCORING_BACKEND": "mock",
            "RUBRIC_SCORING_LOW_CONFIDENCE_THRESHOLD": "0.9",
        }
    )

    with session_factory() as session:
        applicant = Applicant(
            first_name="Low",
            last_name="Confidence",
            email="low@example.edu",
            program_applied="MSc Data Science",
            intake_term="Fall 2026",
            status="submitted",
        )
        session.add(applicant)
        session.flush()
        from app.models import Application

        application = Application(applicant_id=applicant.applicant_id)
        session.add(application)
        session.commit()
        application_id = application.application_id

    add_evidence_document(session_factory, application_id=application_id)
    with session_factory() as session:
        service = MockRubricScoringService(db=session, settings=settings, confidence=0.4)
        outcome = service.score_application(application_id)

    assert outcome.requires_human_review is True
    assert all(score.requires_human_review for score in outcome.criteria)
    assert outcome.recommendation_band == "human_review_required"


def test_protected_attribute_text_is_flagged_and_not_allowed_as_clean_score(
    scoring_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = scoring_client
    application_id = create_application(client, session_factory)
    add_evidence_document(
        session_factory,
        application_id=application_id,
        raw_text="Nationality and religion appear in OCR text but must not be used for scoring.",
    )

    response = client.post(f"/api/v1/applications/{application_id}/score?force=true")

    assert response.status_code == 200
    payload = response.json()
    assert payload["requires_human_review"] is True
    assert "protected_attribute_text_present_do_not_use_for_scoring" in payload["risk_flags"]
    assert all(
        "protected_attribute_text_present_do_not_use_for_scoring" in criterion["risk_flags"]
        for criterion in payload["criteria"]
    )


def test_missing_evidence_does_not_produce_invented_rationale(
    scoring_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = scoring_client
    application_id = create_application(client, session_factory)

    response = client.post(f"/api/v1/applications/{application_id}/score")

    assert response.status_code == 200
    payload = response.json()
    assert payload["requires_human_review"] is True
    for criterion in payload["criteria"]:
        assert criterion["supporting_evidence"] == []
        assert criterion["missing_information"]
        assert "Insufficient evidence" in criterion["rationale"]
        assert criterion["confidence"] <= 0.3
        assert "missing_supporting_evidence" in criterion["risk_flags"]
