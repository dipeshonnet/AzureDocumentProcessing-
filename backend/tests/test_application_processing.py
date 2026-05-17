from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.v1.applications import storage_service_dependency
from app.config import AppSettings, get_settings
from app.db.dependencies import get_db
from app.db.init_db import init_db
from app.db.session import create_db_engine, create_session_factory
from app.main import create_app
from app.models import Applicant, Application, RubricCriterionScore, RubricScorecard
from app.services.storage import LocalStorageService


@pytest.fixture
def processing_client(tmp_path: Path) -> tuple[TestClient, sessionmaker[Session]]:
    database_path = tmp_path / "admissions-processing-test.db"
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
            "MAX_UPLOAD_BYTES": "2048",
            "DOCUMENT_EXTRACTION_BACKEND": "mock",
            "DOCUMENT_CLASSIFICATION_BACKEND": "mock",
            "STRUCTURED_EXTRACTION_BACKEND": "mock",
            "DOCUMENT_SUMMARIZATION_BACKEND": "mock",
            "RUBRIC_SCORING_BACKEND": "mock",
        }
    )

    def override_db() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[storage_service_dependency] = lambda: LocalStorageService(storage_root)
    return TestClient(app), session_factory


def create_application(client: TestClient, session_factory: sessionmaker[Session]) -> str:
    with session_factory() as session:
        applicant = Applicant(
            first_name="Grace",
            last_name="Hopper",
            email="grace@example.edu",
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


def upload_document(
    client: TestClient,
    application_id: str,
    filename: str = "personal_statement.pdf",
    content_type: str = "application/pdf",
) -> str:
    response = client.post(
        f"/api/v1/applications/{application_id}/documents",
        files={"file": (filename, b"file bytes", content_type)},
    )
    assert response.status_code == 201
    return response.json()["document_id"]


def test_process_application_generates_reviewer_ready_scorecard(
    processing_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = processing_client
    application_id = create_application(client, session_factory)
    upload_document(client, application_id, "personal_statement.pdf")

    response = client.post(f"/api/v1/applications/{application_id}/process")

    assert response.status_code == 200
    payload = response.json()
    assert payload["processing_status"] in {"ready_for_review", "needs_manual_review"}
    assert payload["review_status"] == payload["processing_status"]
    assert payload["scorecard_detail"] is not None
    assert payload["scorecard_detail"]["recommendation_band"] != "admit"
    assert payload["scorecard_detail"]["decision_support_summary"]
    assert len(payload["scorecard_detail"]["criteria"]) == 6

    with session_factory() as session:
        application = session.get(Application, application_id)
        scorecard = session.get(RubricScorecard, application_id)
        scores = session.query(RubricCriterionScore).filter_by(application_id=application_id).all()
        assert application is not None
        assert application.processing_status == payload["processing_status"]
        assert scorecard is not None
        assert len(scores) == 6


def test_process_application_without_documents_fails_validation_and_stores_status(
    processing_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = processing_client
    application_id = create_application(client, session_factory)

    response = client.post(f"/api/v1/applications/{application_id}/process")

    assert response.status_code == 400
    status_response = client.get(f"/api/v1/applications/{application_id}/processing-status")
    assert status_response.status_code == 200
    payload = status_response.json()
    assert payload["processing_status"] == "failed"
    assert payload["processing_errors"][0]["step"] == "validation"
    assert payload["processing_errors"][0]["blocking"] is True


def test_process_application_continues_when_one_document_fails(
    processing_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _session_factory = processing_client
    application_id = create_application(client, _session_factory)
    failed_document_id = upload_document(client, application_id, "notes.txt", "text/plain")
    upload_document(client, application_id, "resume.pdf")

    response = client.post(f"/api/v1/applications/{application_id}/process")

    assert response.status_code == 200
    payload = response.json()
    assert payload["processing_status"] == "needs_manual_review"
    assert payload["scorecard_detail"] is not None
    assert any(
        error["document_id"] == failed_document_id
        and error["step"] == "extracting"
        and error["blocking"] is False
        for error in payload["processing_errors"]
    )


def test_process_application_with_only_unprocessable_documents_blocks_scoring(
    processing_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _session_factory = processing_client
    application_id = create_application(client, _session_factory)
    upload_document(client, application_id, "notes.txt", "text/plain")

    response = client.post(f"/api/v1/applications/{application_id}/process")

    assert response.status_code == 200
    payload = response.json()
    assert payload["processing_status"] == "failed"
    assert payload["scorecard_detail"] is None
    assert any(error["step"] == "scoring" and error["blocking"] is True for error in payload["processing_errors"])


def test_process_application_is_idempotent_without_force(
    processing_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = processing_client
    application_id = create_application(client, session_factory)
    upload_document(client, application_id, "official_transcript.pdf")

    first = client.post(f"/api/v1/applications/{application_id}/process")
    second = client.post(f"/api/v1/applications/{application_id}/process")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["scorecard_detail"] is not None

    with session_factory() as session:
        scores = session.query(RubricCriterionScore).filter_by(application_id=application_id).all()
        assert len(scores) == 6
