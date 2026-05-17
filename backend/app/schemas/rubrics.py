from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class RubricCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion_id: NonEmptyString
    name: NonEmptyString
    description: NonEmptyString
    weight: float = Field(gt=0)
    max_score: float = Field(gt=0)
    scoring_levels: dict[int, NonEmptyString]
    evidence_required: list[NonEmptyString] = Field(min_length=1)
    human_review_triggers: list[NonEmptyString] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_scoring_levels(self) -> RubricCriterion:
        required_levels = set(range(1, 6))
        if set(self.scoring_levels.keys()) != required_levels:
            raise ValueError("scoring_levels must include levels 1 through 5")
        return self


class AdmissionsRubric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rubric_id: NonEmptyString
    name: NonEmptyString
    description: NonEmptyString
    version: int = Field(ge=1)
    criteria: list[RubricCriterion] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_weights(self) -> AdmissionsRubric:
        total = sum(criterion.weight for criterion in self.criteria)
        if not (_is_close(total, 1.0) or _is_close(total, 100.0)):
            raise ValueError("rubric criterion weights must sum to 1.0 or 100")
        criterion_ids = [criterion.criterion_id for criterion in self.criteria]
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("criterion_id values must be unique")
        return self


def _is_close(value: float, target: float) -> bool:
    return abs(value - target) <= 0.0001
