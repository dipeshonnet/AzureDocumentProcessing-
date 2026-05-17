from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.v1.applications import storage_service_dependency
from app.api.v1.documents import document_classification_service_dependency
from app.config import AppSettings, get_settings
from app.db.dependencies import get_db
from app.db.init_db import init_db
from app.db.session import create_db_engine, create_session_factory
from app.main import create_app
from app.models import Applicant, ApplicationDocument, ExtractedDocumentContent
from app.services.classification import (
    ClassificationResult,
    MockClassificationService,
    TwoStageClassificationService,
)
from app.services.storage import LocalStorageService


def build_classification_client(
    tmp_path: Path,
    *,
    fallback_result: ClassificationResult | None = None,
) -> tuple[TestClient, sessionmaker[Session]]:
    database_path = tmp_path / "admissions-classification-test.db"
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
            "DOCUMENT_CLASSIFICATION_BACKEND": "mock",
            "CLASSIFICATION_TEXT_MAX_CHARS": "120",
            "CLASSIFICATION_LOW_CONFIDENCE_THRESHOLD": "0.65",
            "RULE_BASED_CLASSIFICATION_MIN_CONFIDENCE": "0.88",
        }
    )

    def override_db() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    def override_classification_service(
        db: Session = Depends(get_db),
    ) -> TwoStageClassificationService:
        return TwoStageClassificationService(
            db=db,
            settings=settings,
            fallback_classifier=MockClassificationService(result=fallback_result),
        )

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[storage_service_dependency] = lambda: LocalStorageService(storage_root)
    app.dependency_overrides[document_classification_service_dependency] = (
        override_classification_service
    )
    return TestClient(app), session_factory


@pytest.fixture
def classification_client(tmp_path: Path) -> tuple[TestClient, sessionmaker[Session]]:
    return build_classification_client(tmp_path)


def create_application(client: TestClient, session_factory: sessionmaker[Session]) -> str:
    with session_factory() as session:
        applicant = Applicant(
            first_name="Dorothy",
            last_name="Vaughan",
            email="dorothy@example.edu",
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


def upload_document(client: TestClient, application_id: str, filename: str) -> str:
    response = client.post(
        f"/api/v1/applications/{application_id}/documents",
        files={"file": (filename, b"file bytes", "application/pdf")},
    )
    assert response.status_code == 201
    return response.json()["document_id"]


def add_extraction(
    session_factory: sessionmaker[Session],
    *,
    document_id: str,
    raw_text: str,
    page_text: str | None = None,
) -> None:
    with session_factory() as session:
        session.add(
            ExtractedDocumentContent(
                document_id=document_id,
                raw_text=raw_text,
                pages=[
                    {
                        "page_number": 1,
                        "text": page_text or raw_text,
                        "lines": [{"content": "General Document"}],
                        "words": [],
                    }
                ],
                tables=[],
                key_value_pairs={"values": {}, "pairs": []},
                extraction_confidence=0.8,
                extraction_metadata={"provider": "test"},
            )
        )
        document = session.get(ApplicationDocument, document_id)
        assert document is not None
        document.processing_status = "extracted"
        session.commit()


def test_classify_obvious_filename_uses_rule_based_path(
    classification_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = classification_client
    application_id = create_application(client, session_factory)
    document_id = upload_document(client, application_id, "official_transcript.pdf")

    response = client.post(f"/api/v1/documents/{document_id}/classify")

    assert response.status_code == 200
    payload = response.json()
    assert payload["document_type"] == "transcript"
    assert payload["confidence"] >= 0.88
    assert payload["requires_human_review"] is False
    assert payload["source"] == "rule_based"

    with session_factory() as session:
        document = session.get(ApplicationDocument, document_id)
        assert document is not None
        assert document.document_type == "transcript"
        assert document.classification_confidence == payload["confidence"]
        assert document.classification_metadata["source"] == "rule_based"
        assert "rationale" in document.classification_metadata


def test_classify_ambiguous_ocr_text_uses_mock_model(tmp_path: Path) -> None:
    fallback = ClassificationResult(
        document_type="recommendation_letter",
        confidence=0.87,
        rationale="Mock model found recommendation-style evidence.",
        evidence_snippets=["I am pleased to comment on the applicant"],
        requires_human_review=False,
        source="mock_model",
    )
    client, session_factory = build_classification_client(tmp_path, fallback_result=fallback)
    application_id = create_application(client, session_factory)
    document_id = upload_document(client, application_id, "uploaded_document.pdf")
    add_extraction(
        session_factory,
        document_id=document_id,
        raw_text="This document is attached for admissions review.",
    )

    response = client.post(f"/api/v1/documents/{document_id}/classify")

    assert response.status_code == 200
    payload = response.json()
    assert payload["document_type"] == "recommendation_letter"
    assert payload["source"] == "mock_model"
    assert payload["evidence_snippets"] == ["I am pleased to comment on the applicant"]


def test_classify_low_confidence_routes_to_human_review(tmp_path: Path) -> None:
    fallback = ClassificationResult(
        document_type="resume_cv",
        confidence=0.42,
        rationale="Mock model is uncertain.",
        evidence_snippets=["Education", "Experience"],
        requires_human_review=False,
        source="mock_model",
    )
    client, session_factory = build_classification_client(tmp_path, fallback_result=fallback)
    application_id = create_application(client, session_factory)
    document_id = upload_document(client, application_id, "unclear_upload.pdf")
    add_extraction(
        session_factory,
        document_id=document_id,
        raw_text="General admissions packet with unclear contents.",
    )

    response = client.post(f"/api/v1/documents/{document_id}/classify")

    assert response.status_code == 200
    payload = response.json()
    assert payload["document_type"] == "resume_cv"
    assert payload["confidence"] == 0.42
    assert payload["requires_human_review"] is True

    with session_factory() as session:
        document = session.get(ApplicationDocument, document_id)
        assert document is not None
        assert document.classification_metadata["requires_human_review"] is True
