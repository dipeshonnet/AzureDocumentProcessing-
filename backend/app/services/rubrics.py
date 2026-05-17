from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from app.config import ROOT_DIR
from app.schemas.rubrics import AdmissionsRubric


DEFAULT_RUBRIC_PATH = ROOT_DIR / "config" / "rubrics" / "default_admissions_rubric.yaml"


class RubricLoadError(Exception):
    """Raised when a rubric cannot be loaded or validated."""


def load_rubric(path: str | Path) -> AdmissionsRubric:
    rubric_path = Path(path)
    try:
        with rubric_path.open("r", encoding="utf-8") as stream:
            payload = yaml.safe_load(stream)
    except OSError as exc:
        raise RubricLoadError(f"Unable to read rubric file: {rubric_path}") from exc

    if not isinstance(payload, dict):
        raise RubricLoadError("Rubric file must contain a YAML object.")

    try:
        return AdmissionsRubric.model_validate(payload)
    except ValidationError as exc:
        raise RubricLoadError(str(exc)) from exc


def load_default_rubric() -> AdmissionsRubric:
    return load_rubric(DEFAULT_RUBRIC_PATH)
