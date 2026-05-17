from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.api.v1.applications import storage_service_dependency
from app.api.v1.documents import structured_extraction_service_dependency
from app.config import AppSettings, get_settings
from app.db.dependencies import get_db
from app.db.init_db import init_db
from app.db.session import create_db_engine, create_session_factory
from app.main import create_app
from app.models import Applicant, ApplicationDocument, ExtractedDocumentContent
from app.schemas.structured_extractions import (
    EvidenceSnippet,
    NumberField,
    RecommendationLetterExtraction,
    ScoreComponent,
    ScoreComponentsField,
    TestScoreExtraction as ScoreReportExtractionSchema,
    TextField,
    TextListField,
    TranscriptExtraction,
)
from app.services.storage import LocalStorageService
from app.services.structured_extraction import MockStructuredExtractionService


def evidence() -> list[EvidenceSnippet]:
    return [EvidenceSnippet(snippet="GPA 3.7 / 4.0", page_number=1, source="ocr")]


def text_field(value: str) -> TextField:
    return TextField(value=value, evidence=evidence(), uncertain=False, explanation=None)


def text_list(values: list[str]) -> TextListField:
    return TextListField(value=values, evidence=evidence(), uncertain=False, explanation=None)


def uncertain_text(explanation: str = "Not found in OCR text.") -> TextField:
    return TextField(value=None, evidence=[], uncertain=True, explanation=explanation)


def uncertain_list(explanation: str = "Not found in OCR text.") -> TextListField:
    return TextListField(value=[], evidence=[], uncertain=True, explanation=explanation)


def transcript_extraction() -> TranscriptExtraction:
    return TranscriptExtraction(
        institution=text_field("Example University"),
        degree=text_field("Bachelor of Science"),
        major=text_field("Computer Science"),
        gpa=NumberField(value=3.7, evidence=evidence(), uncertain=False, explanation=None),
        gpa_scale=NumberField(value=4.0, evidence=evidence(), uncertain=False, explanation=None),
        coursework_highlights=text_list(["Algorithms", "Databases"]),
        academic_honors=text_list(["Dean's List"]),
        academic_risks=uncertain_list(),
        evidence=evidence(),
    )


@pytest.fixture
def structured_client(tmp_path: Path) -> tuple[TestClient, sessionmaker[Session]]:
    database_path = tmp_path / "admissions-structured-test.db"
    storage_root = tmp_path / "storage"
    engine = create_db_engine(f"sqlite:///{database_path}")
    init_db(engine)
    session_factory = create_session_factory(engine)

    settings = AppSettings.from_mapping(
        {
            "APP_ENV": "test",
            "DATABASE_URL": f"sqlite:///{database_path}",
            "STORAGE_BACKEND": "local",
            "LOCAL_STORAGE_ROOT": str(storage_root),
            "MAX_UPLOAD_BYTES": "1024",
            "STRUCTURED_EXTRACTION_BACKEND": "mock",
            "STRUCTURED_EXTRACTION_TEXT_MAX_CHARS": "500",
        }
    )

    def override_db() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    def override_structured_service(
        db: Session = Depends(get_db),
    ) -> MockStructuredExtractionService:
        return MockStructuredExtractionService(
            db=db,
            settings=settings,
            extraction=transcript_extraction(),
        )

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[storage_service_dependency] = lambda: LocalStorageService(storage_root)
    app.dependency_overrides[structured_extraction_service_dependency] = override_structured_service
    return TestClient(app), session_factory


def create_application(client: TestClient, session_factory: sessionmaker[Session]) -> str:
    with session_factory() as session:
        applicant = Applicant(
            first_name="Annie",
            last_name="Easley",
            email="annie@example.edu",
            program_applied="MSc Computer Science",
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


def upload_document(client: TestClient, application_id: str, filename: str = "transcript.pdf") -> str:
    response = client.post(
        f"/api/v1/applications/{application_id}/documents",
        files={"file": (filename, b"file bytes", "application/pdf")},
    )
    assert response.status_code == 201
    return response.json()["document_id"]


def mark_document_extracted(
    session_factory: sessionmaker[Session],
    *,
    document_id: str,
    document_type: str,
) -> None:
    with session_factory() as session:
        document = session.get(ApplicationDocument, document_id)
        assert document is not None
        document.document_type = document_type
        document.classification_confidence = 0.96
        document.classification_metadata = {"source": "test"}
        document.processing_status = "extracted"
        session.add(
            ExtractedDocumentContent(
                document_id=document_id,
                raw_text="Example University transcript. GPA 3.7 / 4.0. Algorithms. Dean's List.",
                pages=[
                    {
                        "page_number": 1,
                        "text": "Example University transcript. GPA 3.7 / 4.0.",
                        "lines": [{"content": "OFFICIAL TRANSCRIPT"}],
                        "words": [],
                    }
                ],
                tables=[],
                key_value_pairs={"values": {}, "pairs": []},
                extraction_confidence=0.9,
                extraction_metadata={"provider": "test"},
            )
        )
        session.commit()


def test_structured_extract_persists_transcript_payload(
    structured_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = structured_client
    application_id = create_application(client, session_factory)
    document_id = upload_document(client, application_id)
    mark_document_extracted(session_factory, document_id=document_id, document_type="transcript")

    response = client.post(f"/api/v1/documents/{document_id}/structured-extract")

    assert response.status_code == 200
    payload = response.json()
    assert payload["document_type"] == "transcript"
    assert payload["extraction"]["institution"]["value"] == "Example University"
    assert payload["extraction"]["gpa"]["value"] == 3.7
    assert payload["metadata"]["source"] == "mock"
    assert payload["metadata"]["requires_human_review"] is True

    with session_factory() as session:
        extracted = session.get(ExtractedDocumentContent, document_id)
        document = session.get(ApplicationDocument, document_id)
        assert extracted is not None
        assert document is not None
        assert extracted.structured_extraction["document_type"] == "transcript"
        assert extracted.structured_extraction["extraction"]["degree"]["value"] == "Bachelor of Science"
        assert document.processing_status == "structured_extracted"


def test_structured_extract_rejects_unsupported_document_type(
    structured_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = structured_client
    application_id = create_application(client, session_factory)
    document_id = upload_document(client, application_id)
    mark_document_extracted(session_factory, document_id=document_id, document_type="application_form")

    response = client.post(f"/api/v1/documents/{document_id}/structured-extract")

    assert response.status_code == 400
    assert "not supported" in response.json()["detail"]


def test_transcript_schema_rejects_gpa_greater_than_scale() -> None:
    with pytest.raises(ValidationError):
        TranscriptExtraction(
            institution=text_field("Example University"),
            degree=text_field("Bachelor of Science"),
            major=text_field("Computer Science"),
            gpa=NumberField(value=4.2, evidence=evidence(), uncertain=False, explanation=None),
            gpa_scale=NumberField(value=4.0, evidence=evidence(), uncertain=False, explanation=None),
            coursework_highlights=text_list(["Algorithms"]),
            academic_honors=uncertain_list(),
            academic_risks=uncertain_list(),
            evidence=evidence(),
        )


def test_test_score_schema_rejects_negative_scores() -> None:
    with pytest.raises(ValidationError):
        ScoreReportExtractionSchema(
            test_name=text_field("GRE"),
            score=NumberField(value=-1, evidence=evidence(), uncertain=False, explanation=None),
            score_components=ScoreComponentsField(
                value=[
                    ScoreComponent(
                        name="Quant",
                        score=160,
                        evidence=evidence(),
                        uncertain=False,
                        explanation=None,
                    )
                ],
                evidence=evidence(),
                uncertain=False,
                explanation=None,
            ),
            test_date=text_field("2026-01-01"),
            evidence=evidence(),
        )


def test_recommendation_strength_requires_explanation_when_empty() -> None:
    with pytest.raises(ValidationError):
        RecommendationLetterExtraction(
            recommender_name=text_field("Dr. Reviewer"),
            recommender_role=text_field("Professor"),
            relationship_to_applicant=text_field("Instructor"),
            recommendation_strength=TextField(value=None, evidence=[], uncertain=True, explanation=None),
            academic_ability=text_field("Strong"),
            character_traits=text_list(["Persistent"]),
            leadership_or_teamwork=text_field("Led team project"),
            concerns_or_caveats=uncertain_list(),
            evidence=evidence(),
        )
