from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.v1.applications import storage_service_dependency
from app.api.v1.documents import summarization_service_dependency
from app.config import AppSettings, get_settings
from app.db.dependencies import get_db
from app.db.init_db import init_db
from app.db.session import create_db_engine, create_session_factory
from app.main import create_app
from app.models import Applicant, ApplicationDocument, DocumentSummary, ExtractedDocumentContent
from app.services.storage import LocalStorageService
from app.services.summarization import (
    DocumentSummaryResult,
    MockSummarizationService,
    SectionSummary,
    SummaryEvidence,
)


def make_summary(short_summary: str = "Applicant document summary.") -> DocumentSummaryResult:
    evidence = [
        SummaryEvidence(
            snippet="The applicant completed advanced coursework.",
            page_number=1,
            source="ocr",
        )
    ]
    return DocumentSummaryResult(
        short_summary=short_summary,
        section_summaries=[
            SectionSummary(
                section_title="Academic content",
                summary="The document includes academic review information.",
                evidence=evidence,
            )
        ],
        strengths=["Advanced coursework is visible in OCR text."],
        concerns=[],
        missing_information=[],
        reviewer_attention_points=["Verify evidence against the source document."],
        evidence=evidence,
    )


@pytest.fixture
def summary_client(
    tmp_path: Path,
) -> tuple[TestClient, sessionmaker[Session], dict[str, DocumentSummaryResult | None]]:
    database_path = tmp_path / "admissions-summary-test.db"
    storage_root = tmp_path / "storage"
    engine = create_db_engine(f"sqlite:///{database_path}")
    init_db(engine)
    session_factory = create_session_factory(engine)
    result_holder: dict[str, DocumentSummaryResult | None] = {"result": make_summary()}

    settings = AppSettings.from_mapping(
        {
            "APP_ENV": "test",
            "DATABASE_URL": f"sqlite:///{database_path}",
            "STORAGE_BACKEND": "local",
            "LOCAL_STORAGE_ROOT": str(storage_root),
            "MAX_UPLOAD_BYTES": "1024",
            "DOCUMENT_SUMMARIZATION_BACKEND": "mock",
            "DOCUMENT_SUMMARY_TEXT_MAX_CHARS": "500",
            "DOCUMENT_SUMMARY_LOW_CONFIDENCE_THRESHOLD": "0.5",
        }
    )

    def override_db() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    def override_summarization_service(
        db: Session = Depends(get_db),
    ) -> MockSummarizationService:
        return MockSummarizationService(
            db=db,
            settings=settings,
            result=result_holder["result"],
        )

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[storage_service_dependency] = lambda: LocalStorageService(storage_root)
    app.dependency_overrides[summarization_service_dependency] = override_summarization_service
    return TestClient(app), session_factory, result_holder


def create_application(client: TestClient, session_factory: sessionmaker[Session]) -> str:
    with session_factory() as session:
        applicant = Applicant(
            first_name="Christine",
            last_name="Darden",
            email="christine@example.edu",
            program_applied="MSc Aerospace Engineering",
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


def upload_document(client: TestClient, application_id: str) -> str:
    response = client.post(
        f"/api/v1/applications/{application_id}/documents",
        files={"file": ("transcript.pdf", b"file bytes", "application/pdf")},
    )
    assert response.status_code == 201
    return response.json()["document_id"]


def add_extraction(
    session_factory: sessionmaker[Session],
    *,
    document_id: str,
    raw_text: str = "The applicant completed advanced coursework.",
    page_text: str | None = "The applicant completed advanced coursework.",
    confidence: float | None = 0.9,
) -> None:
    with session_factory() as session:
        document = session.get(ApplicationDocument, document_id)
        assert document is not None
        document.document_type = "transcript"
        document.processing_status = "extracted"
        session.add(
            ExtractedDocumentContent(
                document_id=document_id,
                raw_text=raw_text,
                pages=(
                    [
                        {
                            "page_number": 1,
                            "text": page_text,
                            "lines": [{"content": "Academic Record"}],
                            "words": [],
                        }
                    ]
                    if page_text is not None
                    else []
                ),
                tables=[],
                key_value_pairs={"values": {}, "pairs": []},
                extraction_confidence=confidence,
                extraction_metadata={"provider": "test"},
            )
        )
        session.commit()


def test_summarize_document_persists_mock_summary(
    summary_client: tuple[TestClient, sessionmaker[Session], dict[str, DocumentSummaryResult | None]],
) -> None:
    client, session_factory, _holder = summary_client
    application_id = create_application(client, session_factory)
    document_id = upload_document(client, application_id)
    add_extraction(session_factory, document_id=document_id)

    response = client.post(f"/api/v1/documents/{document_id}/summarize")

    assert response.status_code == 200
    payload = response.json()
    assert payload["document_id"] == document_id
    assert payload["short_summary"] == "Applicant document summary."
    assert payload["section_summaries"][0]["evidence"]
    assert payload["evidence"][0]["snippet"]

    with session_factory() as session:
        summary = session.get(DocumentSummary, document_id)
        document = session.get(ApplicationDocument, document_id)
        assert summary is not None
        assert document is not None
        assert summary.short_summary == "Applicant document summary."
        assert document.processing_status == "summarized"


def test_summarize_missing_document_extraction_returns_400(
    summary_client: tuple[TestClient, sessionmaker[Session], dict[str, DocumentSummaryResult | None]],
) -> None:
    client, session_factory, _holder = summary_client
    application_id = create_application(client, session_factory)
    document_id = upload_document(client, application_id)

    response = client.post(f"/api/v1/documents/{document_id}/summarize")

    assert response.status_code == 400
    assert "OCR extraction is required" in response.json()["detail"]


def test_summarize_unreadable_document_says_so_clearly(
    summary_client: tuple[TestClient, sessionmaker[Session], dict[str, DocumentSummaryResult | None]],
) -> None:
    client, session_factory, _holder = summary_client
    application_id = create_application(client, session_factory)
    document_id = upload_document(client, application_id)
    add_extraction(
        session_factory,
        document_id=document_id,
        raw_text="",
        page_text=None,
        confidence=None,
    )

    response = client.post(f"/api/v1/documents/{document_id}/summarize")

    assert response.status_code == 200
    payload = response.json()
    assert "unreadable" in payload["short_summary"].lower()
    assert payload["concerns"]
    assert payload["evidence"][0]["source"] == "ocr_quality"


def test_summarize_low_confidence_document_says_so_clearly(
    summary_client: tuple[TestClient, sessionmaker[Session], dict[str, DocumentSummaryResult | None]],
) -> None:
    client, session_factory, _holder = summary_client
    application_id = create_application(client, session_factory)
    document_id = upload_document(client, application_id)
    add_extraction(
        session_factory,
        document_id=document_id,
        raw_text="Some faint OCR text.",
        page_text="Some faint OCR text.",
        confidence=0.2,
    )

    response = client.post(f"/api/v1/documents/{document_id}/summarize")

    assert response.status_code == 200
    payload = response.json()
    assert "low-confidence" in payload["short_summary"].lower()
    assert payload["reviewer_attention_points"]


def test_summarize_is_idempotent_unless_forced(
    summary_client: tuple[TestClient, sessionmaker[Session], dict[str, DocumentSummaryResult | None]],
) -> None:
    client, session_factory, holder = summary_client
    application_id = create_application(client, session_factory)
    document_id = upload_document(client, application_id)
    add_extraction(session_factory, document_id=document_id)

    first = client.post(f"/api/v1/documents/{document_id}/summarize")
    holder["result"] = make_summary("Regenerated summary.")
    second = client.post(f"/api/v1/documents/{document_id}/summarize")
    forced = client.post(f"/api/v1/documents/{document_id}/summarize?force=true")

    assert first.status_code == 200
    assert second.status_code == 200
    assert forced.status_code == 200
    assert second.json()["short_summary"] == "Applicant document summary."
    assert forced.json()["short_summary"] == "Regenerated summary."
