from __future__ import annotations

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = Field(..., examples=["ok"])


class ConfigRequirementStatus(BaseModel):
    name: str
    configured: bool
    secret: bool


class ConfigCheckResponse(BaseModel):
    ready: bool
    environment: str
    required: list[ConfigRequirementStatus]
    missing: list[str]
    safeguards: dict[str, bool]
