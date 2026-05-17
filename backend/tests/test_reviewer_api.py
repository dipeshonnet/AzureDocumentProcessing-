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
from app.models import Applicant, Application
from app.services.storage import LocalStorageService


@pytest.fixture
def review_client(tmp_path: Path) -> tuple[TestClient, sessionmaker[Session]]:
    database_path = tmp_path / "admissions-review-test.db"
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
            "AZURE_OPENAI_CLASSIFICATION_MODEL_DEPLOYMENT": "classifier-test-deployment",
            "AZURE_OPENAI_STRUCTURED_EXTRACTION_MODEL_DEPLOYMENT": "structured-test-deployment",
            "AZURE_OPENAI_SUMMARIZATION_MODEL_DEPLOYMENT": "summary-test-deployment",
            "AZURE_OPENAI_RUBRIC_SCORING_MODEL_DEPLOYMENT": "scoring-test-deployment",
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


def create_processed_application(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> str:
    with session_factory() as session:
        applicant = Applicant(
            first_name="Ida",
            last_name="Rhodes",
            email="ida@example.edu",
            program_applied="MSc Computer Science",
            intake_term="Fall 2026",
            status="submitted",
        )
        session.add(applicant)
        session.commit()
        session.refresh(applicant)
        applicant_id = applicant.applicant_id

    create_response = client.post("/api/v1/applications", json={"applicant_id": applicant_id})
    assert create_response.status_code == 201
    application_id = create_response.json()["application_id"]
    upload_response = client.post(
        f"/api/v1/applications/{application_id}/documents",
        files={"file": ("personal_statement.pdf", b"file bytes", "application/pdf")},
    )
    assert upload_response.status_code == 201
    process_response = client.post(f"/api/v1/applications/{application_id}/process")
    assert process_response.status_code == 200
    return application_id


def test_reviewer_scorecard_response_contains_review_packet(
    review_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = review_client
    application_id = create_processed_application(client, session_factory)

    response = client.get(f"/api/v1/review/applications/{application_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["applicant"]["email"] == "ida@example.edu"
    assert len(payload["documents"]) == 1
    assert payload["documents"][0]["classification"]["document_type"] == "personal_statement"
    assert payload["documents"][0]["summary"]["short_summary"]
    assert payload["documents"][0]["structured_extracted_fields"]["document_type"] == "personal_statement"
    assert len(payload["rubric_criterion_scores"]) == 6
    assert payload["supporting_evidence"]
    assert payload["confidence"]["average_rubric_confidence"] is not None
    assert payload["weighted_score"] is not None
    assert payload["model_versions"]["rubric_scoring_backend"] == "mock"
    assert payload["prompt_versions"]["rubric_scoring"] == "v1"
    assert payload["documents"][0]["prompt_versions"]["document_summary"] == "v1"
    assert payload["ai_recommendation_is_final_decision"] is False
    assert payload["decision_support_only"] is True

    list_response = client.get("/api/v1/review/applications")
    assert list_response.status_code == 200
    assert list_response.json()[0]["application_id"] == application_id


def test_criterion_override_requires_reason(
    review_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = review_client
    application_id = create_processed_application(client, session_factory)

    response = client.post(
        f"/api/v1/review/applications/{application_id}/criterion/academic_readiness/override",
        json={"score": 4.0},
    )

    assert response.status_code == 422


def test_criterion_override_stores_reason_and_audit_metadata(
    review_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = review_client
    application_id = create_processed_application(client, session_factory)

    response = client.post(
        f"/api/v1/review/applications/{application_id}/criterion/academic_readiness/override",
        json={
            "score": 4.0,
            "rationale": "Reviewer verified the transcript evidence manually.",
            "reason": "Manual transcript review found stronger evidence than the advisory score reflected.",
            "reviewer_id": "reviewer-1",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["reviewer_override"]["score"] == 4.0
    assert payload["reviewer_override"]["reason"]
    assert payload["reviewer_override"]["audit_metadata"]["ai_score_preserved"] is True
    assert payload["audit_metadata"][-1]["action"] == "criterion_override_recorded"

    detail = client.get(f"/api/v1/review/applications/{application_id}").json()
    assert "academic_readiness" in detail["reviewer_overrides"]["criteria"]


def test_final_decision_persistence_is_separate_from_ai_recommendation(
    review_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = review_client
    application_id = create_processed_application(client, session_factory)

    response = client.post(
        f"/api/v1/review/applications/{application_id}/decision",
        json={
            "decision": "waitlist",
            "reason": "Committee review complete.",
            "reviewer_id": "reviewer-2",
            "ai_recommendation_acknowledged": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["final_human_decision"] == "waitlist"
    assert payload["ai_recommendation_is_final_decision"] is False
    assert payload["audit_metadata"][-1]["metadata"]["stored_separately_from_ai_scorecard"] is True

    with session_factory() as session:
        application = session.get(Application, application_id)
        assert application is not None
        assert application.final_human_decision == "waitlist"
        assert application.scorecard is not None
        assert application.scorecard.recommendation_band != "waitlist"


def test_notes_endpoint_records_audit_metadata(
    review_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = review_client
    application_id = create_processed_application(client, session_factory)

    response = client.post(
        f"/api/v1/review/applications/{application_id}/notes",
        json={"notes": "Reviewer checked original document evidence.", "reviewer_id": "reviewer-3"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["reviewer_notes"] == "Reviewer checked original document evidence."
    assert payload["audit_metadata"][-1]["action"] == "reviewer_notes_updated"
