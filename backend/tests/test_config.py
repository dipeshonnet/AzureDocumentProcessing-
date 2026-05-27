from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import AppSettings, get_settings
from app.main import create_app
from app.services.config_check import build_config_check


COMPLETE_CONFIG = {
    "APP_ENV": "test",
    "DATABASE_URL": "sqlite:///./test.db",
    "AZURE_STORAGE_ACCOUNT_NAME": "storageacct",
    "AZURE_STORAGE_CONTAINER_DOCUMENTS": "documents",
    "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT": "https://doc-intelligence.example.test",
    "AZURE_DOCUMENT_INTELLIGENCE_KEY": "secret-doc-key",
    "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT": "https://foundry.example.test",
    "AZURE_OPENAI_ENDPOINT": "https://openai.example.test",
    "AZURE_OPENAI_API_KEY": "secret-openai-key",
    "AZURE_OPENAI_API_VERSION": "2026-01-01",
    "AZURE_OPENAI_CLASSIFICATION_MODEL_DEPLOYMENT": "classification-model",
    "AZURE_OPENAI_STRUCTURED_EXTRACTION_MODEL_DEPLOYMENT": "structured-extraction-model",
    "AZURE_OPENAI_SUMMARIZATION_MODEL_DEPLOYMENT": "summarization-model",
    "AZURE_OPENAI_RUBRIC_SCORING_MODEL_DEPLOYMENT": "rubric-scoring-model",
    "AZURE_OPENAI_REVIEW_MODEL_DEPLOYMENT": "review-model",
    "AZURE_AI_AGENT_DOCUMENT_CLASSIFICATION_NAME": "classifier",
    "AZURE_AI_AGENT_DOCUMENT_CLASSIFICATION_VERSION": "1",
    "AZURE_AI_AGENT_DOCUMENT_CLASSIFICATION_MODEL": "classification-model",
    "AZURE_AI_AGENT_TRANSCRIPT_EXTRACTION_NAME": "transcript-extractor",
    "AZURE_AI_AGENT_TRANSCRIPT_EXTRACTION_VERSION": "1",
    "AZURE_AI_AGENT_TRANSCRIPT_EXTRACTION_MODEL": "transcript-model",
    "AZURE_AI_AGENT_RECOMMENDATION_EXTRACTION_NAME": "recommendation-extractor",
    "AZURE_AI_AGENT_RECOMMENDATION_EXTRACTION_VERSION": "1",
    "AZURE_AI_AGENT_RECOMMENDATION_EXTRACTION_MODEL": "recommendation-model",
    "AZURE_AI_AGENT_ESSAY_EXTRACTION_NAME": "essay-extractor",
    "AZURE_AI_AGENT_ESSAY_EXTRACTION_VERSION": "1",
    "AZURE_AI_AGENT_ESSAY_EXTRACTION_MODEL": "essay-model",
    "REQUIRE_HUMAN_FINAL_DECISION": "true",
    "AI_DECISION_SUPPORT_ONLY": "true",
}


def test_settings_load_from_environment_mapping() -> None:
    settings = AppSettings.from_mapping(COMPLETE_CONFIG)

    assert settings.app_env == "test"
    assert settings.database_url == "sqlite:///./test.db"
    assert settings.azure_openai_review_model_deployment == "review-model"
    assert settings.require_human_final_decision is True
    assert settings.ai_decision_support_only is True


def test_config_check_reports_missing_values_without_secret_values() -> None:
    settings = AppSettings.from_mapping({})

    report = build_config_check(settings)
    serialized = report.model_dump()

    assert report.ready is False
    assert "DATABASE_URL" in report.missing
    assert "AZURE_OPENAI_API_KEY" in report.missing
    assert report.database.configured is False
    assert report.database.required_tables["saved_rubrics"] is False
    assert "secret-openai-key" not in str(serialized)
    assert "secret-doc-key" not in str(serialized)


def test_config_check_endpoint_reports_ready_for_complete_config() -> None:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: AppSettings.from_mapping(COMPLETE_CONFIG)
    client = TestClient(app)

    response = client.get("/api/v1/system/config-check")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ready"] is True
    assert payload["missing"] == []
    assert all("value" not in item for item in payload["required"])
    assert payload["database"]["configured"] is True
    assert payload["database"]["dialect"] == "sqlite"
    assert payload["database"]["checked"] is False
