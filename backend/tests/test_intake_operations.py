from __future__ import annotations

import time
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.api.v1.applications import storage_service_dependency
from app.config import AppSettings, get_settings
from app.db.dependencies import get_db
from app.db.init_db import init_db
from app.db.session import create_db_engine, create_session_factory
from app.main import create_app
from app.models import Applicant, Application, ApplicationDocument, AuthSession, IntakeJob, LocalUser
from app.security import UserRole
from app.services.intake_jobs import reset_interrupted_jobs
from app.services.local_auth import create_session, hash_password
from app.services.storage import LocalStorageService


@pytest.fixture
def intake_client(tmp_path: Path) -> tuple[TestClient, sessionmaker[Session], Path]:
    database_path = tmp_path / "intake-test.db"
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
            "MAX_UPLOAD_BYTES": "4096",
            "PARSER_MODE": "mock",
            "INTAKE_MOCK_PROCESSING_DELAY_SECONDS": "0",
            "INTAKE_WORKER_ENABLED": "true",
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


@pytest.fixture
def live_intake_client(tmp_path: Path) -> tuple[TestClient, sessionmaker[Session], Path]:
    database_path = tmp_path / "live-intake-test.db"
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
            "MAX_UPLOAD_BYTES": "4096",
            "DOCUMENT_EXTRACTION_BACKEND": "mock",
            "DOCUMENT_CLASSIFICATION_BACKEND": "mock",
            "STRUCTURED_EXTRACTION_BACKEND": "mock",
            "DOCUMENT_SUMMARIZATION_BACKEND": "mock",
            "RUBRIC_SCORING_BACKEND": "mock",
            "PARSER_MODE": "azure",
            "INTAKE_MOCK_PROCESSING_DELAY_SECONDS": "0",
            "INTAKE_WORKER_ENABLED": "true",
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


def auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"email": "superadmin", "password": "EverydayAI"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


def wait_for_terminal(client: TestClient, job_id: str, headers: dict[str, str]) -> dict:
    deadline = time.time() + 3
    latest: dict | None = None
    while time.time() < deadline:
        response = client.get(f"/api/jobs/{job_id}", headers=headers)
        assert response.status_code == 200
        latest = response.json()
        if latest["status"] in {"completed", "failed"}:
            return latest
        time.sleep(0.05)
    raise AssertionError(f"Job did not finish. Latest payload: {latest}")


def test_demo_login_and_me_work(intake_client: tuple[TestClient, sessionmaker[Session], Path]) -> None:
    client, _session_factory, _storage_root = intake_client

    response = client.post("/api/auth/login", json={"email": "superadmin", "password": "EverydayAI"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["user"]["email"] == "superadmin"
    assert payload["user"]["role"] == "admin"
    assert payload["user"]["billing_rate_per_unit"] == 1.0
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {payload['token']}"})
    assert me.status_code == 200
    assert me.json()["email"] == "superadmin"


def test_admin_can_manage_user_billing_rate(intake_client: tuple[TestClient, sessionmaker[Session], Path]) -> None:
    client, _session_factory, _storage_root = intake_client
    headers = auth_headers(client)

    users = client.get("/api/auth/users", headers=headers)

    assert users.status_code == 200
    superadmin = users.json()[0]
    assert superadmin["email"] == "superadmin"
    assert superadmin["billing_rate_per_unit"] == 1.0

    updated = client.put(
        f"/api/auth/users/{superadmin['user_id']}/billing-rate",
        headers=headers,
        json={"billing_rate_per_unit": 2.5},
    )

    assert updated.status_code == 200
    assert updated.json()["billing_rate_per_unit"] == 2.5


def test_authenticated_user_can_change_password(intake_client: tuple[TestClient, sessionmaker[Session], Path]) -> None:
    client, _session_factory, _storage_root = intake_client
    headers = auth_headers(client)

    response = client.put(
        "/api/auth/password",
        headers=headers,
        json={
            "current_password": "EverydayAI",
            "new_password": "EverydayAI2",
            "confirm_new_password": "EverydayAI2",
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    old_login = client.post("/api/auth/login", json={"email": "superadmin", "password": "EverydayAI"})
    assert old_login.status_code == 401
    new_login = client.post("/api/auth/login", json={"email": "superadmin", "password": "EverydayAI2"})
    assert new_login.status_code == 200


def test_authenticated_user_can_update_profile_logo(intake_client: tuple[TestClient, sessionmaker[Session], Path]) -> None:
    client, _session_factory, _storage_root = intake_client
    headers = auth_headers(client)
    logo_data_url = "data:image/png;base64,bG9nbw=="

    response = client.put(
        "/api/auth/profile-logo",
        headers=headers,
        json={"university_logo_data_url": logo_data_url},
    )

    assert response.status_code == 200
    assert response.json()["university_logo_data_url"] == logo_data_url

    invalid = client.put(
        "/api/auth/profile-logo",
        headers=headers,
        json={"university_logo_data_url": "data:image/svg+xml;base64,PHN2Zy8+"},
    )

    assert invalid.status_code == 400


def test_registration_creates_local_reviewer(intake_client: tuple[TestClient, sessionmaker[Session], Path]) -> None:
    client, _session_factory, _storage_root = intake_client

    response = client.post(
        "/api/auth/register",
        json={
            "email": "reviewer@example.edu",
            "password": "EverydayAI2",
            "confirm_password": "EverydayAI2",
        },
    )

    assert response.status_code == 201
    assert response.json()["user"]["role"] == "admissions_reviewer"


def test_unauthenticated_upload_is_rejected(intake_client: tuple[TestClient, sessionmaker[Session], Path]) -> None:
    client, _session_factory, _storage_root = intake_client

    response = client.post(
        "/api/upload",
        data={"applicant_name": "No Token", "program_applied": "MSc Data Science"},
        files={"file": ("statement.txt", b"statement", "text/plain")},
    )

    assert response.status_code == 401


def test_upload_creates_admissions_records_and_intake_job(
    intake_client: tuple[TestClient, sessionmaker[Session], Path],
) -> None:
    client, session_factory, storage_root = intake_client
    headers = auth_headers(client)

    response = client.post(
        "/api/upload",
        headers=headers,
        data={
            "applicant_name": "Ada Lovelace",
            "applicant_id": "ADA-2026",
            "program_applied": "MSc Analytics",
            "rubric_id": "default_admissions_rubric",
        },
        files={"file": ("transcript.pdf", b"private transcript text", "application/pdf")},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] in {"queued", "processing", "completed"}
    assert payload["applicant_name"] == "Ada Lovelace"
    assert payload["program_applied"] == "MSc Analytics"

    with session_factory() as session:
        applicant = session.get(Applicant, "ADA-2026")
        document = session.get(ApplicationDocument, payload["document_id"])
        job = session.get(IntakeJob, payload["job_id"])
        assert applicant is not None
        assert document is not None
        assert job is not None
        assert (storage_root / document.blob_url_or_path).exists()


def test_worker_transitions_job_to_completed(intake_client: tuple[TestClient, sessionmaker[Session], Path]) -> None:
    client, _session_factory, _storage_root = intake_client
    headers = auth_headers(client)
    response = client.post(
        "/api/upload",
        headers=headers,
        data={"applicant_name": "Grace Hopper", "program_applied": "MSc Computer Science"},
        files={"file": ("resume.pdf", b"resume", "application/pdf")},
    )

    terminal = wait_for_terminal(client, response.json()["job_id"], headers)

    assert terminal["status"] == "completed"
    assert terminal["progress"] == 100
    assert terminal["extracted_text"]
    assert terminal["section_analysis"]


def test_live_intake_mode_runs_application_processing_pipeline(
    live_intake_client: tuple[TestClient, sessionmaker[Session], Path],
) -> None:
    client, session_factory, _storage_root = live_intake_client
    headers = auth_headers(client)
    response = client.post(
        "/api/upload",
        headers=headers,
        data={"applicant_name": "Live Staging", "program_applied": "MSc Data Science"},
        files={"file": ("official_transcript.pdf", b"transcript", "application/pdf")},
    )

    terminal = wait_for_terminal(client, response.json()["job_id"], headers)

    assert terminal["status"] == "completed"
    assert terminal["parser_mode"] == "azure"
    assert terminal["extracted_text"] == "Mock extracted document text."
    assert terminal["summary"] == "Mock summary grounded in extracted document text."
    assert len(terminal["section_analysis"]) == 6
    assert terminal["extracted_record"]["decision_support_only"] is True
    assert terminal["extracted_record"]["scorecard"]["recommendation_band"]

    with session_factory() as session:
        document = session.get(ApplicationDocument, terminal["document_id"])
        assert document is not None
        assert document.processing_status in {"summarized", "structured_extracted"}
        assert document.extracted_content is not None


def test_parser_failure_marks_only_job_failed(intake_client: tuple[TestClient, sessionmaker[Session], Path]) -> None:
    client, _session_factory, _storage_root = intake_client
    headers = auth_headers(client)
    response = client.post(
        "/api/upload",
        headers=headers,
        data={"applicant_name": "Fail Case", "program_applied": "MSc Data Science"},
        files={"file": ("parser_fail.pdf", b"broken", "application/pdf")},
    )

    terminal = wait_for_terminal(client, response.json()["job_id"], headers)

    assert terminal["status"] == "failed"
    assert "Mock parser" in terminal["status_message"]


def test_unsupported_file_is_rejected_without_creating_job(
    intake_client: tuple[TestClient, sessionmaker[Session], Path],
) -> None:
    client, session_factory, _storage_root = intake_client
    headers = auth_headers(client)

    response = client.post(
        "/api/upload",
        headers=headers,
        data={"applicant_name": "Unsupported", "program_applied": "MSc Data Science"},
        files={"file": ("script.exe", b"nope", "application/octet-stream")},
    )

    assert response.status_code == 400
    with session_factory() as session:
        assert session.scalar(select(IntakeJob)) is None


def test_auditor_cannot_upload(intake_client: tuple[TestClient, sessionmaker[Session], Path]) -> None:
    client, session_factory, _storage_root = intake_client
    with session_factory() as session:
        user = LocalUser(
            email="auditor@example.edu",
            password_hash=hash_password("EverydayAI2"),
            role=UserRole.READ_ONLY_AUDITOR.value,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        token = create_session(db=session, user=user)

    response = client.post(
        "/api/upload",
        headers={"Authorization": f"Bearer {token}"},
        data={"applicant_name": "Auditor", "program_applied": "MSc Data Science"},
        files={"file": ("statement.txt", b"statement", "text/plain")},
    )

    assert response.status_code == 403


def test_reset_interrupted_jobs_requeues_processing_jobs() -> None:
    engine = create_db_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        applicant = Applicant(
            first_name="Restart",
            last_name="Case",
            email="restart@example.edu",
            program_applied="MSc Data Science",
            intake_term="Current intake",
            status="submitted",
        )
        application = Application(applicant=applicant)
        document = ApplicationDocument(
            application=application,
            original_filename="transcript.pdf",
            blob_url_or_path="applications/app/raw/doc/transcript.pdf",
            processing_status="uploaded",
        )
        processing_job = IntakeJob(
            application=application,
            document=document,
            status="processing",
            progress=45,
            status_message="Parsing document.",
        )
        queued_job = IntakeJob(
            application=application,
            document=document,
            status="queued",
            progress=5,
            status_message="Waiting.",
        )
        session.add_all([applicant, processing_job, queued_job])
        session.commit()

        requeued = reset_interrupted_jobs(session)

        assert set(requeued) == {processing_job.job_id, queued_job.job_id}
        refreshed = session.get(IntakeJob, processing_job.job_id)
        assert refreshed is not None
        assert refreshed.status == "queued"
        assert refreshed.status_message == "Processing was interrupted. Waiting to restart."
