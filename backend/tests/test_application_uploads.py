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
from app.models import Applicant, ApplicationDocument
from app.services.storage import LocalStorageService


@pytest.fixture
def upload_client(tmp_path: Path) -> tuple[TestClient, sessionmaker[Session], Path]:
    database_path = tmp_path / "admissions-test.db"
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
            "MAX_UPLOAD_BYTES": "32",
        }
    )

    def override_db() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[storage_service_dependency] = lambda: LocalStorageService(storage_root)

    return TestClient(app), session_factory, storage_root


def create_applicant(session_factory: sessionmaker[Session]) -> Applicant:
    with session_factory() as session:
        applicant = Applicant(
            first_name="Katherine",
            last_name="Johnson",
            email="katherine@example.edu",
            program_applied="MSc Applied Mathematics",
            intake_term="Fall 2026",
            status="submitted",
        )
        session.add(applicant)
        session.commit()
        session.refresh(applicant)
        return applicant


def test_create_and_get_application(upload_client: tuple[TestClient, sessionmaker[Session], Path]) -> None:
    client, session_factory, _storage_root = upload_client
    applicant = create_applicant(session_factory)

    create_response = client.post(
        "/api/v1/applications",
        json={"applicant_id": applicant.applicant_id},
    )

    assert create_response.status_code == 201
    created = create_response.json()
    assert created["applicant_id"] == applicant.applicant_id
    assert created["final_human_decision"] is None

    get_response = client.get(f"/api/v1/applications/{created['application_id']}")

    assert get_response.status_code == 200
    assert get_response.json()["application_id"] == created["application_id"]


def test_create_application_rejects_final_decision(
    upload_client: tuple[TestClient, sessionmaker[Session], Path],
) -> None:
    client, session_factory, _storage_root = upload_client
    applicant = create_applicant(session_factory)

    response = client.post(
        "/api/v1/applications",
        json={
            "applicant_id": applicant.applicant_id,
            "final_human_decision": "admit",
        },
    )

    assert response.status_code == 400
    assert "Final human decisions" in response.json()["detail"]


def test_upload_document_stores_file_and_metadata_without_logging_content(
    upload_client: tuple[TestClient, sessionmaker[Session], Path],
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, session_factory, storage_root = upload_client
    applicant = create_applicant(session_factory)
    application = client.post("/api/v1/applications", json={"applicant_id": applicant.applicant_id}).json()
    file_content = b"private transcript text"

    with caplog.at_level("INFO"):
        response = client.post(
            f"/api/v1/applications/{application['application_id']}/documents",
            files={"file": ("My Transcript.PDF", file_content, "application/pdf")},
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["original_filename"] == "My_Transcript.pdf"
    assert payload["processing_status"] == "uploaded"
    assert payload["blob_url_or_path"].startswith(
        f"applications/{application['application_id']}/raw/{payload['document_id']}/"
    )

    stored_path = storage_root / payload["blob_url_or_path"]
    assert stored_path.read_bytes() == file_content
    assert file_content.decode("utf-8") not in caplog.text

    with session_factory() as session:
        document = session.get(ApplicationDocument, payload["document_id"])
        assert document is not None
        assert document.blob_url_or_path == payload["blob_url_or_path"]


def test_list_application_documents(upload_client: tuple[TestClient, sessionmaker[Session], Path]) -> None:
    client, session_factory, _storage_root = upload_client
    applicant = create_applicant(session_factory)
    application = client.post("/api/v1/applications", json={"applicant_id": applicant.applicant_id}).json()

    client.post(
        f"/api/v1/applications/{application['application_id']}/documents",
        files={"file": ("statement.txt", b"statement", "text/plain")},
    )

    response = client.get(f"/api/v1/applications/{application['application_id']}/documents")

    assert response.status_code == 200
    documents = response.json()
    assert len(documents) == 1
    assert documents[0]["original_filename"] == "statement.txt"


def test_upload_rejects_unsupported_file_type(
    upload_client: tuple[TestClient, sessionmaker[Session], Path],
) -> None:
    client, session_factory, _storage_root = upload_client
    applicant = create_applicant(session_factory)
    application = client.post("/api/v1/applications", json={"applicant_id": applicant.applicant_id}).json()

    response = client.post(
        f"/api/v1/applications/{application['application_id']}/documents",
        files={"file": ("script.exe", b"nope", "application/octet-stream")},
    )

    assert response.status_code == 400
    assert "Unsupported file type" in response.json()["detail"]


def test_upload_rejects_file_over_size_limit(
    upload_client: tuple[TestClient, sessionmaker[Session], Path],
) -> None:
    client, session_factory, _storage_root = upload_client
    applicant = create_applicant(session_factory)
    application = client.post("/api/v1/applications", json={"applicant_id": applicant.applicant_id}).json()

    response = client.post(
        f"/api/v1/applications/{application['application_id']}/documents",
        files={"file": ("large.pdf", b"x" * 33, "application/pdf")},
    )

    assert response.status_code == 413
    assert "size limit" in response.json()["detail"]
