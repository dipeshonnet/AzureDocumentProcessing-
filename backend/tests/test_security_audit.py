from __future__ import annotations

import json
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
from app.models import Applicant, AuditLog
from app.services.audit import AuditAction
from app.services.storage import LocalStorageService


REVIEWER_HEADERS = {
    "x-dev-actor": "reviewer-audit-1",
    "x-dev-role": "admissions_reviewer",
}


@pytest.fixture
def security_client(tmp_path: Path) -> tuple[TestClient, sessionmaker[Session]]:
    database_path = tmp_path / "security-audit.db"
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


def create_application_with_scorecard(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> str:
    with session_factory() as session:
        applicant = Applicant(
            first_name="Audit",
            last_name="Candidate",
            email="audit@example.edu",
            program_applied="MSc Computer Science",
            intake_term="Fall 2026",
            status="submitted",
        )
        session.add(applicant)
        session.commit()
        session.refresh(applicant)
        applicant_id = applicant.applicant_id

    create_response = client.post(
        "/api/v1/applications",
        json={"applicant_id": applicant_id},
        headers=REVIEWER_HEADERS,
    )
    assert create_response.status_code == 201
    application_id = create_response.json()["application_id"]
    upload_response = client.post(
        f"/api/v1/applications/{application_id}/documents",
        files={"file": ("statement.pdf", b"safe bytes", "application/pdf")},
        headers=REVIEWER_HEADERS,
    )
    assert upload_response.status_code == 201
    process_response = client.post(
        f"/api/v1/applications/{application_id}/process",
        headers=REVIEWER_HEADERS,
    )
    assert process_response.status_code == 200
    return application_id


def test_override_audit_log(
    security_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = security_client
    application_id = create_application_with_scorecard(client, session_factory)

    response = client.post(
        f"/api/v1/review/applications/{application_id}/criterion/academic_readiness/override",
        json={
            "score": 4.0,
            "reason": "Reviewer verified supporting materials.",
            "rationale": "Manual review confirmed stronger academic evidence.",
        },
        headers=REVIEWER_HEADERS,
    )

    assert response.status_code == 200
    with session_factory() as session:
        audit = session.scalar(
            select(AuditLog)
            .where(AuditLog.action == AuditAction.SCORE_OVERRIDE)
            .order_by(AuditLog.timestamp.desc())
        )
        assert audit is not None
        assert audit.actor == "reviewer-audit-1"
        assert audit.actor_role == "admissions_reviewer"
        assert audit.application_id == application_id
        assert audit.document_id is None
        assert audit.timestamp is not None
        assert audit.old_value["criterion_id"] == "academic_readiness"
        assert audit.new_value["criterion_id"] == "academic_readiness"
        assert audit.new_value["reason_present"] is True
        assert audit.metadata_["ai_score_preserved"] is True


def test_final_decision_audit_log(
    security_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = security_client
    application_id = create_application_with_scorecard(client, session_factory)

    response = client.post(
        f"/api/v1/review/applications/{application_id}/decision",
        json={
            "decision": "defer",
            "reason": "Committee requested another review round.",
            "ai_recommendation_acknowledged": True,
        },
        headers=REVIEWER_HEADERS,
    )

    assert response.status_code == 200
    with session_factory() as session:
        audit = session.scalar(
            select(AuditLog)
            .where(AuditLog.action == AuditAction.FINAL_DECISION)
            .order_by(AuditLog.timestamp.desc())
        )
        assert audit is not None
        assert audit.actor == "reviewer-audit-1"
        assert audit.application_id == application_id
        assert audit.old_value == {"final_human_decision": None}
        assert audit.new_value == {"final_human_decision": "defer"}
        assert audit.metadata_["stored_separately_from_ai_scorecard"] is True


def test_config_check_does_not_leak_secret_values() -> None:
    secret_values = {
        "APP_SECRET_KEY": "app-secret-value",
        "AZURE_STORAGE_CONNECTION_STRING": "storage-secret-value",
        "AZURE_DOCUMENT_INTELLIGENCE_KEY": "doc-intel-secret-value",
        "AZURE_OPENAI_API_KEY": "openai-secret-value",
        "AZURE_LANGUAGE_KEY": "language-secret-value",
    }
    settings = AppSettings.from_mapping(
        {
            "APP_ENV": "test",
            "DATABASE_URL": "sqlite:///./test.db",
            **secret_values,
        }
    )
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    response = client.get("/api/v1/system/config-check")

    assert response.status_code == 200
    serialized = json.dumps(response.json())
    for secret in secret_values.values():
        assert secret not in serialized


def test_raw_document_text_not_written_to_normal_logs(
    security_client: tuple[TestClient, sessionmaker[Session]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, session_factory = security_client
    with session_factory() as session:
        applicant = Applicant(
            first_name="No",
            last_name="Leak",
            email="noleak@example.edu",
            program_applied="MSc Computer Science",
            intake_term="Fall 2026",
            status="submitted",
        )
        session.add(applicant)
        session.commit()
        session.refresh(applicant)
        applicant_id = applicant.applicant_id

    application = client.post(
        "/api/v1/applications",
        json={"applicant_id": applicant_id},
        headers=REVIEWER_HEADERS,
    ).json()
    document = client.post(
        f"/api/v1/applications/{application['application_id']}/documents",
        files={"file": ("transcript.pdf", b"stored binary bytes", "application/pdf")},
        headers=REVIEWER_HEADERS,
    ).json()

    with caplog.at_level("INFO"):
        response = client.post(
            f"/api/v1/documents/{document['document_id']}/extract",
            headers=REVIEWER_HEADERS,
        )

    assert response.status_code == 200
    assert "Mock extracted document text." not in caplog.text
    assert "stored binary bytes" not in caplog.text

    with session_factory() as session:
        audit = session.scalar(
            select(AuditLog).where(AuditLog.action == AuditAction.DOCUMENT_EXTRACTION)
        )
        assert audit is not None
        serialized = json.dumps(
            {
                "old_value": audit.old_value,
                "new_value": audit.new_value,
                "metadata": audit.metadata_,
            }
        )
        assert "Mock extracted document text." not in serialized


def test_read_only_auditor_cannot_perform_write_actions(
    security_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = security_client
    application_id = create_application_with_scorecard(client, session_factory)

    response = client.post(
        f"/api/v1/review/applications/{application_id}/decision",
        json={
            "decision": "defer",
            "reason": "Attempted by auditor.",
            "ai_recommendation_acknowledged": True,
        },
        headers={
            "x-dev-actor": "auditor-1",
            "x-dev-role": "read_only_auditor",
        },
    )

    assert response.status_code == 403
