from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.v1.applications import storage_service_dependency
from app.api.v1.documents import document_extraction_service_dependency
from app.config import AppSettings, get_settings
from app.db.dependencies import get_db
from app.db.init_db import init_db
from app.db.session import create_db_engine, create_session_factory
from app.main import create_app
from app.models import Applicant, Application, ApplicationDocument, ExtractedDocumentContent
from app.services.document_extraction import (
    AzureDocumentExtractionError,
    DocumentExtractionService,
    EmptyOcrResultError,
    ExtractionResult,
    ExtractionTimeoutError,
    MockDocumentExtractionService,
)
from app.services.storage import LocalStorageService


class FakeExtractionService:
    def __init__(
        self,
        result: ExtractionResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result or ExtractionResult(
            raw_text="Extracted transcript text",
            pages=[
                {
                    "page_number": 1,
                    "text": "Extracted transcript text",
                    "lines": [{"content": "Extracted transcript text"}],
                    "words": [{"content": "Extracted", "confidence": 0.95}],
                }
            ],
            tables=[
                {
                    "row_count": 1,
                    "column_count": 2,
                    "cells": [{"row_index": 0, "column_index": 0, "content": "Course"}],
                }
            ],
            key_value_pairs={
                "values": {"GPA": "3.8"},
                "pairs": [{"key": "GPA", "value": "3.8", "confidence": 0.9}],
            },
            extraction_confidence=0.95,
            extraction_metadata={"provider": "fake", "page_count": 1},
        )
        self.error = error
        self.calls = 0

    def extract_document(self, document_id: str) -> ExtractedDocumentContent:
        self.calls += 1
        if self.error:
            raise self.error
        return ExtractedDocumentContent(
            document_id=document_id,
            raw_text=self.result.raw_text,
            pages=self.result.pages,
            tables=self.result.tables,
            key_value_pairs=self.result.key_value_pairs,
            extraction_confidence=self.result.extraction_confidence,
            extraction_metadata=self.result.extraction_metadata,
        )

    def extract_from_file(self, path_or_stream: object, content_type: str) -> ExtractionResult:
        return self.result

    def extract_from_blob(self, blob_path: str) -> ExtractionResult:
        return self.result


class FakeStorageReader:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.read_paths: list[str] = []

    def read_bytes(self, *, storage_path: str) -> bytes:
        self.read_paths.append(storage_path)
        return self.content


@pytest.fixture
def extraction_client(
    tmp_path: Path,
) -> tuple[TestClient, sessionmaker[Session], dict[str, DocumentExtractionService | None]]:
    database_path = tmp_path / "admissions-extraction-test.db"
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
            "DOCUMENT_EXTRACTION_BACKEND": "mock",
        }
    )
    service_holder: dict[str, DocumentExtractionService | None] = {"service": FakeExtractionService()}

    def override_db() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[storage_service_dependency] = lambda: LocalStorageService(storage_root)
    app.dependency_overrides[document_extraction_service_dependency] = lambda: service_holder["service"]

    return TestClient(app), session_factory, service_holder


@pytest.fixture
def default_extraction_client(
    tmp_path: Path,
) -> tuple[TestClient, sessionmaker[Session]]:
    database_path = tmp_path / "admissions-default-extraction-test.db"
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
            "DOCUMENT_EXTRACTION_BACKEND": "mock",
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
            first_name="Mary",
            last_name="Jackson",
            email="mary@example.edu",
            program_applied="MSc Engineering",
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


def test_extract_document_persists_ocr_payload_and_updates_document(
    extraction_client: tuple[TestClient, sessionmaker[Session], dict[str, DocumentExtractionService | None]],
) -> None:
    client, session_factory, service_holder = extraction_client
    application_id = create_application(client, session_factory)
    document_id = upload_document(client, application_id)

    response = client.post(f"/api/v1/documents/{document_id}/extract")

    assert response.status_code == 200
    payload = response.json()
    assert payload["document_id"] == document_id
    assert payload["raw_text"] == "Extracted transcript text"
    assert payload["pages"][0]["page_number"] == 1
    assert payload["tables"][0]["row_count"] == 1
    assert payload["key_value_pairs"]["values"]["GPA"] == "3.8"
    assert service_holder["service"].calls == 1

    with session_factory() as session:
        document = session.get(ApplicationDocument, document_id)
        extracted = session.get(ExtractedDocumentContent, document_id)
        assert document is not None
        assert document.page_count == 1
        assert document.processing_status == "extracted"
        assert extracted is not None
        assert extracted.extraction_confidence == 0.95


def test_extract_document_is_idempotent_without_force(
    extraction_client: tuple[TestClient, sessionmaker[Session], dict[str, DocumentExtractionService | None]],
) -> None:
    client, session_factory, service_holder = extraction_client
    application_id = create_application(client, session_factory)
    document_id = upload_document(client, application_id)

    first_service = service_holder["service"]
    first_response = client.post(f"/api/v1/documents/{document_id}/extract")
    assert first_response.status_code == 200

    second_service = FakeExtractionService(
        result=ExtractionResult(raw_text="Changed text", pages=[{"page_number": 1, "text": "Changed text"}])
    )
    service_holder["service"] = second_service
    second_response = client.post(f"/api/v1/documents/{document_id}/extract")

    assert second_response.status_code == 200
    assert second_response.json()["raw_text"] == "Extracted transcript text"
    assert first_service.calls == 1
    assert second_service.calls == 0


def test_extract_document_force_refreshes_existing_extraction(
    extraction_client: tuple[TestClient, sessionmaker[Session], dict[str, DocumentExtractionService | None]],
) -> None:
    client, session_factory, service_holder = extraction_client
    application_id = create_application(client, session_factory)
    document_id = upload_document(client, application_id)

    assert client.post(f"/api/v1/documents/{document_id}/extract").status_code == 200
    forced_service = FakeExtractionService(
        result=ExtractionResult(
            raw_text="Forced re-extraction text",
            pages=[{"page_number": 1, "text": "Forced re-extraction text"}],
            extraction_metadata={"provider": "fake", "page_count": 1, "forced": True},
        )
    )
    service_holder["service"] = forced_service

    response = client.post(f"/api/v1/documents/{document_id}/extract?force=true")

    assert response.status_code == 200
    assert response.json()["raw_text"] == "Forced re-extraction text"
    assert forced_service.calls == 1


def test_extract_document_returns_404_for_unknown_document(
    extraction_client: tuple[TestClient, sessionmaker[Session], dict[str, DocumentExtractionService | None]],
) -> None:
    client, _session_factory, _service_holder = extraction_client

    response = client.post("/api/v1/documents/missing-document/extract")

    assert response.status_code == 404


def test_extract_document_returns_400_for_unsupported_file_type(
    default_extraction_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = default_extraction_client
    application_id = create_application(client, session_factory)
    document_id = upload_document(client, application_id, filename="notes.txt")

    response = client.post(f"/api/v1/documents/{document_id}/extract")

    assert response.status_code == 400
    assert "Unsupported document type" in response.json()["detail"]


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (AzureDocumentExtractionError("service failed"), 502),
        (ExtractionTimeoutError("timed out"), 504),
        (EmptyOcrResultError("empty"), 422),
    ],
)
def test_extract_document_maps_service_errors(
    extraction_client: tuple[TestClient, sessionmaker[Session], dict[str, DocumentExtractionService | None]],
    error: Exception,
    expected_status: int,
) -> None:
    client, session_factory, service_holder = extraction_client
    application_id = create_application(client, session_factory)
    document_id = upload_document(client, application_id)
    service_holder["service"] = FakeExtractionService(error=error)

    response = client.post(f"/api/v1/documents/{document_id}/extract")

    assert response.status_code == expected_status


def test_default_extraction_reads_private_storage_bytes(monkeypatch, tmp_path: Path) -> None:
    database_path = tmp_path / "private-storage-extraction.db"
    engine = create_db_engine(f"sqlite:///{database_path}")
    init_db(engine)
    session_factory = create_session_factory(engine)
    settings = AppSettings.from_mapping(
        {
            "APP_ENV": "test",
            "DATABASE_URL": f"sqlite:///{database_path}",
            "STORAGE_BACKEND": "azure",
            "AZURE_STORAGE_ACCOUNT_NAME": "admissiondocstorage1803",
            "AZURE_STORAGE_CONTAINER_DOCUMENTS": "admissions-raw",
            "AZURE_STORAGE_CONNECTION_STRING": "UseDevelopmentStorage=true",
            "DOCUMENT_EXTRACTION_BACKEND": "mock",
        }
    )
    storage_reader = FakeStorageReader(content=b"%PDF private bytes")
    monkeypatch.setattr(
        "app.services.document_extraction.get_storage_service",
        lambda _settings: storage_reader,
    )

    with session_factory() as session:
        applicant = Applicant(
            first_name="Private",
            last_name="Blob",
            email="private@example.edu",
            program_applied="MSc Data Science",
            intake_term="Fall 2026",
            status="submitted",
        )
        application = Application(applicant=applicant)
        document = ApplicationDocument(
            application=application,
            original_filename="transcript.pdf",
            blob_url_or_path="applications/app/raw/doc/transcript.pdf",
        )
        session.add_all([application, document])
        session.commit()
        document_id = document.document_id

        service = MockDocumentExtractionService(db=session, settings=settings)
        extracted = service.extract_document(document_id)

    assert extracted.raw_text == "Mock extracted document text."
    assert storage_reader.read_paths == ["applications/app/raw/doc/transcript.pdf"]
