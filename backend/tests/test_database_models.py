from __future__ import annotations

from sqlalchemy import inspect

from app.db.init_db import init_db
from app.db.session import create_db_engine, create_session_factory
from app.models import (
    Applicant,
    Application,
    ApplicationDocument,
    DocumentSummary,
    ExtractedDocumentContent,
    RubricCriterionScore,
    RubricScorecard,
)


def test_init_db_creates_admissions_tables() -> None:
    engine = create_db_engine("sqlite:///:memory:")

    init_db(engine)

    table_names = set(inspect(engine).get_table_names())
    assert {
        "applicants",
        "applications",
        "application_documents",
        "extracted_document_contents",
        "document_summaries",
        "rubric_criterion_scores",
        "rubric_scorecards",
    }.issubset(table_names)


def test_can_persist_core_admissions_record_graph() -> None:
    engine = create_db_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = create_session_factory(engine)

    with session_factory() as session:
        applicant = Applicant(
            first_name="Grace",
            last_name="Hopper",
            email="grace@example.edu",
            program_applied="MSc Data Science",
            intake_term="Spring 2027",
            status="submitted",
        )
        application = Application(
            applicant=applicant,
            processing_status="processing",
            review_status="pending",
        )
        document = ApplicationDocument(
            application=application,
            original_filename="transcript.pdf",
            blob_url_or_path="local/transcript.pdf",
            document_type="transcript",
            classification_confidence=0.94,
            processing_status="extracted",
            page_count=2,
        )
        document.extracted_content = ExtractedDocumentContent(
            raw_text="Coursework and grades",
            pages=[{"page_number": 1, "text": "Coursework"}],
            tables=[{"rows": [["Course", "Grade"], ["Algorithms", "A"]]}],
            key_value_pairs={"gpa": "3.9"},
            extraction_confidence=0.91,
            extraction_metadata={"provider": "mock"},
        )
        document.summary = DocumentSummary(
            section_summaries=[{"section": "academics", "summary": "Strong coursework"}],
            strengths=["Strong GPA"],
            concerns=[],
            missing_information=["Official seal verification"],
            evidence=[{"document_id": document.document_id, "page": 1}],
        )
        application.rubric_scores.append(
            RubricCriterionScore(
                criterion_name="Academic preparation",
                score=4.5,
                max_score=5,
                weight=0.6,
                rationale="Transcript indicates strong preparation.",
                supporting_evidence=[{"document_id": document.document_id, "page": 1}],
                confidence=0.85,
                requires_human_review=True,
            )
        )
        application.scorecard = RubricScorecard(
            weighted_score=86,
            max_score=100,
            recommendation_band="review_recommended",
            decision_support_summary="Decision support only; human review required.",
            reviewer_override=None,
        )

        session.add(applicant)
        session.commit()

        persisted = session.get(Application, application.application_id)

        assert persisted is not None
        assert persisted.applicant.email == "grace@example.edu"
        assert persisted.documents[0].extracted_content.key_value_pairs["gpa"] == "3.9"
        assert persisted.scorecard.recommendation_band == "review_recommended"
