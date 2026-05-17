from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT_DIR / ".env"


REQUIRED_CONFIG_KEYS: tuple[str, ...] = (
    "DATABASE_URL",
    "AZURE_STORAGE_ACCOUNT_NAME",
    "AZURE_STORAGE_CONTAINER_DOCUMENTS",
    "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT",
    "AZURE_DOCUMENT_INTELLIGENCE_KEY",
    "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_API_VERSION",
    "AZURE_OPENAI_CLASSIFICATION_MODEL_DEPLOYMENT",
    "AZURE_OPENAI_STRUCTURED_EXTRACTION_MODEL_DEPLOYMENT",
    "AZURE_OPENAI_SUMMARIZATION_MODEL_DEPLOYMENT",
    "AZURE_OPENAI_RUBRIC_SCORING_MODEL_DEPLOYMENT",
    "AZURE_OPENAI_REVIEW_MODEL_DEPLOYMENT",
    "AZURE_AI_AGENT_DOCUMENT_CLASSIFICATION_NAME",
    "AZURE_AI_AGENT_DOCUMENT_CLASSIFICATION_VERSION",
    "AZURE_AI_AGENT_DOCUMENT_CLASSIFICATION_MODEL",
    "AZURE_AI_AGENT_TRANSCRIPT_EXTRACTION_NAME",
    "AZURE_AI_AGENT_TRANSCRIPT_EXTRACTION_VERSION",
    "AZURE_AI_AGENT_TRANSCRIPT_EXTRACTION_MODEL",
    "AZURE_AI_AGENT_RECOMMENDATION_EXTRACTION_NAME",
    "AZURE_AI_AGENT_RECOMMENDATION_EXTRACTION_VERSION",
    "AZURE_AI_AGENT_RECOMMENDATION_EXTRACTION_MODEL",
    "AZURE_AI_AGENT_ESSAY_EXTRACTION_NAME",
    "AZURE_AI_AGENT_ESSAY_EXTRACTION_VERSION",
    "AZURE_AI_AGENT_ESSAY_EXTRACTION_MODEL",
)

SECRET_CONFIG_KEYS: frozenset[str] = frozenset(
    {
        "APP_SECRET_KEY",
        "AZURE_STORAGE_CONNECTION_STRING",
        "AZURE_DOCUMENT_INTELLIGENCE_KEY",
        "AZURE_LANGUAGE_KEY",
        "AZURE_OPENAI_API_KEY",
        "APPLICATIONINSIGHTS_CONNECTION_STRING",
    }
)


class AppSettings(BaseSettings):
    """Application settings loaded from environment variables.

    Required Azure values are intentionally optional at startup. The
    config-check endpoint reports readiness without blocking local health checks.
    """

    app_env: str = "local"
    app_name: str = "Admissions AI Reviewer API"
    app_secret_key: str | None = None
    backend_cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    log_level: str = "INFO"
    storage_backend: str = "local"
    local_storage_root: str = "./storage"
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    document_extraction_backend: str = "mock"
    document_extraction_timeout_seconds: int = Field(default=120, gt=0)
    document_classification_backend: str = "mock"
    classification_text_max_chars: int = Field(default=6000, gt=0)
    classification_low_confidence_threshold: float = Field(default=0.65, ge=0, le=1)
    rule_based_classification_min_confidence: float = Field(default=0.88, ge=0, le=1)
    structured_extraction_backend: str = "mock"
    structured_extraction_text_max_chars: int = Field(default=12000, gt=0)
    document_summarization_backend: str = "mock"
    document_summary_text_max_chars: int = Field(default=12000, gt=0)
    document_summary_low_confidence_threshold: float = Field(default=0.5, ge=0, le=1)
    rubric_scoring_backend: str = "mock"
    rubric_scoring_text_max_chars: int = Field(default=16000, gt=0)
    rubric_scoring_low_confidence_threshold: float = Field(default=0.65, ge=0, le=1)
    parser_mode: str = "mock"
    intake_mock_processing_delay_seconds: float = Field(default=0.15, ge=0)
    intake_worker_enabled: bool = True
    dev_auth_enabled: bool = True
    dev_auth_default_actor: str = "dev-local-reviewer"
    dev_auth_default_role: str = "admissions_reviewer"
    pii_redaction_backend: str = "none"
    azure_language_endpoint: str | None = None
    azure_language_key: str | None = None
    applicationinsights_connection_string: str | None = None
    auto_create_db_schema: bool = False

    database_url: str | None = None

    azure_storage_account_name: str | None = None
    azure_storage_container_documents: str | None = None
    azure_storage_connection_string: str | None = None

    azure_document_intelligence_endpoint: str | None = None
    azure_document_intelligence_key: str | None = None

    azure_ai_foundry_project_endpoint: str | None = None

    azure_openai_endpoint: str | None = None
    azure_openai_api_key: str | None = None
    azure_openai_api_version: str | None = None
    azure_openai_classification_model_deployment: str | None = None
    azure_openai_structured_extraction_model_deployment: str | None = None
    azure_openai_summarization_model_deployment: str | None = None
    azure_openai_rubric_scoring_model_deployment: str | None = None
    azure_openai_review_model_deployment: str | None = None

    azure_ai_agent_document_classification_name: str | None = None
    azure_ai_agent_document_classification_version: str | None = None
    azure_ai_agent_document_classification_model: str | None = None

    azure_ai_agent_transcript_extraction_name: str | None = None
    azure_ai_agent_transcript_extraction_version: str | None = None
    azure_ai_agent_transcript_extraction_model: str | None = None

    azure_ai_agent_recommendation_extraction_name: str | None = None
    azure_ai_agent_recommendation_extraction_version: str | None = None
    azure_ai_agent_recommendation_extraction_model: str | None = None

    azure_ai_agent_essay_extraction_name: str | None = None
    azure_ai_agent_essay_extraction_version: str | None = None
    azure_ai_agent_essay_extraction_model: str | None = None

    require_human_final_decision: bool = True
    ai_decision_support_only: bool = True

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def cors_origins(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.backend_cors_origins.split(",")
            if origin.strip()
        ]

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "AppSettings":
        """Build settings from an explicit mapping for deterministic tests."""

        normalized = {key.lower(): value for key, value in values.items()}
        return cls(_env_file=None, **normalized)

    def is_configured(self, env_name: str) -> bool:
        value = getattr(self, env_name.lower(), None)
        if isinstance(value, str):
            return bool(value.strip())
        return value is not None


def configure_logging(log_level: str) -> None:
    level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


@lru_cache
def get_settings() -> AppSettings:
    return AppSettings()
