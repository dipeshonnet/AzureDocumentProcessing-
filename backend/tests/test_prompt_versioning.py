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
from app.models import Applicant, ApplicationDocument, DocumentSummary, ExtractedDocumentContent, RubricCriterionScore
from app.services.prompts import load_prompt
from app.services.storage import LocalStorageService


@pytest.fixture
def prompt_client(tmp_path: Path) -> tuple[TestClient, sessionmaker[Session]]:
    database_path = tmp_path / "admissions-prompts-test.db"
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


def create_processed_application(client: TestClient, session_factory: sessionmaker[Session]) -> str:
    with session_factory() as session:
        applicant = Applicant(
            first_name="Prompt",
            last_name="Version",
            email="prompt-version@example.edu",
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


def test_prompt_templates_include_required_metadata() -> None:
    template = load_prompt("document_classification")

    assert template.prompt_name == "document_classification"
    assert template.prompt_version == "v1"
    assert template.input_schema == "ClassificationPayload"
    assert template.output_schema == "DocumentClassificationResult"
    assert template.safety_constraints


def test_prompt_version_is_persisted_for_llm_outputs(
    prompt_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = prompt_client
    application_id = create_processed_application(client, session_factory)

    with session_factory() as session:
        document = (
            session.query(ApplicationDocument)
            .filter_by(application_id=application_id)
            .one()
        )
        extracted = session.get(ExtractedDocumentContent, document.document_id)
        summary = session.get(DocumentSummary, document.document_id)
        score = (
            session.query(RubricCriterionScore)
            .filter_by(application_id=application_id, criterion_id="academic_readiness")
            .one()
        )

        assert document.classification_metadata["prompt_name"] == "document_classification"
        assert document.classification_metadata["prompt_version"] == "v1"
        assert extracted is not None
        assert extracted.structured_extraction["metadata"]["prompt_name"] == "personal_statement_extraction"
        assert extracted.structured_extraction["metadata"]["prompt_version"] == "v1"
        assert summary is not None
        assert summary.summary_metadata["prompt_name"] == "document_summary"
        assert summary.summary_metadata["prompt_version"] == "v1"
        assert score.scoring_metadata["prompt_name"] == "rubric_scoring"
        assert score.scoring_metadata["prompt_version"] == "v1"
