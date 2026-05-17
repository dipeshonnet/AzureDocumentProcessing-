from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.rubrics import DEFAULT_RUBRIC_PATH, RubricLoadError, load_rubric


def write_yaml(path: Path, payload: dict) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def default_payload() -> dict:
    with DEFAULT_RUBRIC_PATH.open("r", encoding="utf-8") as stream:
        payload = yaml.safe_load(stream)
    assert isinstance(payload, dict)
    return payload


def test_valid_default_rubric_loads() -> None:
    rubric = load_rubric(DEFAULT_RUBRIC_PATH)

    assert rubric.rubric_id == "default_admissions_rubric"
    assert len(rubric.criteria) == 6
    assert sum(criterion.weight for criterion in rubric.criteria) == pytest.approx(1.0)
    assert {criterion.criterion_id for criterion in rubric.criteria} == {
        "academic_readiness",
        "writing_quality",
        "program_fit",
        "recommendation_strength",
        "leadership_and_experience",
        "risk_and_missing_information",
    }


def test_invalid_weights_raise_load_error(tmp_path: Path) -> None:
    payload = default_payload()
    payload["criteria"][0]["weight"] = 0.99
    rubric_path = tmp_path / "invalid_weights.yaml"
    write_yaml(rubric_path, payload)

    with pytest.raises(RubricLoadError, match="weights"):
        load_rubric(rubric_path)


def test_missing_scoring_levels_raise_load_error(tmp_path: Path) -> None:
    payload = default_payload()
    payload["criteria"][0].pop("scoring_levels")
    rubric_path = tmp_path / "missing_scoring_levels.yaml"
    write_yaml(rubric_path, payload)

    with pytest.raises(RubricLoadError, match="scoring_levels"):
        load_rubric(rubric_path)


def test_default_rubric_endpoint_returns_valid_rubric() -> None:
    client = TestClient(create_app())

    response = client.get("/api/v1/rubrics/default")

    assert response.status_code == 200
    payload = response.json()
    assert payload["rubric_id"] == "default_admissions_rubric"
    assert len(payload["criteria"]) == 6
