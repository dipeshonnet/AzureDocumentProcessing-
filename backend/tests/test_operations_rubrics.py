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
from app.models import IntakeJob
from app.services.storage import LocalStorageService


@pytest.fixture
def rubric_client(tmp_path: Path) -> tuple[TestClient, sessionmaker[Session]]:
    database_path = tmp_path / "rubrics-test.db"
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


def auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"email": "superadmin", "password": "EverydayAI"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


def sample_rubric_payload() -> dict:
    return {
        "rubric_id": "custom_nursing_rubric",
        "name": "Custom nursing rubric",
        "description": "Program-specific rubric with GPA rows and recommendation sub-components.",
        "total_points": 100,
        "sections": [
            {
                "section_id": "gpa",
                "name": "GPA",
                "description": "Science GPA tiers.",
                "max_points": 30,
                "components": [
                    {
                        "component_id": "science_gpa",
                        "name": "Science GPA",
                        "description": "Transcript evidence.",
                        "max_points": 20,
                        "rows": [
                            {
                                "row_id": "science_gpa_375_400",
                                "label": "Science GPA",
                                "condition": "3.75-4.0",
                                "points": "20",
                                "notes": "",
                            },
                            {
                                "row_id": "science_gpa_below_300",
                                "label": "Science GPA",
                                "condition": "<3.0",
                                "points": "0",
                                "notes": "",
                            },
                        ],
                    }
                ],
            },
            {
                "section_id": "recommendation",
                "name": "Letters of recommendation",
                "description": "Score two letters only.",
                "max_points": 20,
                "components": [
                    {
                        "component_id": "reference_type",
                        "name": "Reference Type",
                        "description": "Reference category.",
                        "max_points": 5,
                        "rows": [
                            {
                                "row_id": "reference_healthcare",
                                "label": "Healthcare professional/other professional",
                                "condition": "",
                                "points": "5",
                                "notes": "",
                            }
                        ],
                    },
                    {
                        "component_id": "lor_overall",
                        "name": "LOR Overall Evaluation",
                        "description": "Overall strength.",
                        "max_points": 5,
                        "rows": [
                            {
                                "row_id": "lor_excellent",
                                "label": "Excellent (5)",
                                "condition": "",
                                "points": "5",
                                "notes": "",
                            }
                        ],
                    },
                ],
            },
        ],
    }


def test_rubric_list_seeds_default_template(rubric_client: tuple[TestClient, sessionmaker[Session]]) -> None:
    client, _session_factory = rubric_client
    response = client.get("/api/rubrics", headers=auth_headers(client))

    assert response.status_code == 200
    payload = response.json()
    assert payload
    assert payload[0]["rubric_id"] == "default_admissions_rubric"
    assert any(section["components"] for section in payload[0]["sections"])


def test_can_save_rubric_with_rows_and_sub_components(
    rubric_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _session_factory = rubric_client
    headers = auth_headers(client)

    response = client.post("/api/rubrics", headers=headers, json=sample_rubric_payload())

    assert response.status_code == 201
    payload = response.json()
    assert payload["rubric_id"] == "custom_nursing_rubric"
    assert len(payload["sections"][1]["components"]) == 2
    assert payload["sections"][0]["components"][0]["rows"][0]["condition"] == "3.75-4.0"

    update_payload = payload | {"name": "Custom nursing rubric v2", "version": 2}
    update_response = client.put("/api/rubrics/custom_nursing_rubric", headers=headers, json=update_payload)

    assert update_response.status_code == 200
    assert update_response.json()["version"] == 2


def test_upload_uses_selected_saved_rubric(
    rubric_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = rubric_client
    headers = auth_headers(client)
    create_response = client.post("/api/rubrics", headers=headers, json=sample_rubric_payload())
    assert create_response.status_code == 201

    upload_response = client.post(
        "/api/upload",
        headers=headers,
        data={
            "applicant_name": "Rubric User",
            "program_applied": "BSN",
            "rubric_id": "custom_nursing_rubric",
        },
        files={"file": ("transcript.pdf", b"transcript", "application/pdf")},
    )

    assert upload_response.status_code == 201
    assert upload_response.json()["rubric_id"] == "custom_nursing_rubric"
    with session_factory() as session:
        job = session.get(IntakeJob, upload_response.json()["job_id"])
        assert job is not None
        assert job.rubric_id == "custom_nursing_rubric"


def test_upload_rejects_unknown_rubric(rubric_client: tuple[TestClient, sessionmaker[Session]]) -> None:
    client, _session_factory = rubric_client

    response = client.post(
        "/api/upload",
        headers=auth_headers(client),
        data={
            "applicant_name": "Unknown Rubric",
            "program_applied": "BSN",
            "rubric_id": "not_a_real_rubric",
        },
        files={"file": ("statement.txt", b"statement", "text/plain")},
    )

    assert response.status_code == 400
    assert "rubric" in response.json()["detail"].lower()
