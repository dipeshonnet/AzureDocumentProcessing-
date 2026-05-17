# Admissions AI Production Environment Variables

This file documents production configuration. Store secret values in Azure Key Vault and inject them into the runtime with managed identity or platform secret references. Do not commit real values to source control.

## Core Runtime

```text
APP_ENV=production
APP_NAME=Admissions AI Reviewer API
BACKEND_CORS_ORIGINS=https://<frontend-hostname>
LOG_LEVEL=INFO
DATABASE_URL=postgresql+psycopg://<user>:<password>@<host>:5432/<database>?sslmode=require
AUTO_CREATE_DB_SCHEMA=false
REQUIRE_HUMAN_FINAL_DECISION=true
AI_DECISION_SUPPORT_ONLY=true
```

`AUTO_CREATE_DB_SCHEMA` must remain `false` in production. Run database migrations as a controlled deployment step.

## Development Auth Replacement

The current `DEV_AUTH_*` settings are for local development only.

```text
DEV_AUTH_ENABLED=false
DEV_AUTH_DEFAULT_ACTOR=
DEV_AUTH_DEFAULT_ROLE=
```

Before production launch, replace development header auth with the institution-approved identity provider and role mapping for:

```text
admin
admissions_reviewer
read_only_auditor
```

## Storage

```text
STORAGE_BACKEND=azure
AZURE_STORAGE_ACCOUNT_NAME=<storage-account-name>
AZURE_STORAGE_CONTAINER_DOCUMENTS=admissions-raw
AZURE_STORAGE_CONNECTION_STRING=<key-vault-secret-or-managed-identity-alternative>
MAX_UPLOAD_BYTES=10485760
```

Prefer managed identity and least-privilege role assignments over connection strings when the storage implementation is expanded.

## Azure AI Document Intelligence

```text
DOCUMENT_EXTRACTION_BACKEND=azure
DOCUMENT_EXTRACTION_TIMEOUT_SECONDS=120
AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT=https://<resource-name>.cognitiveservices.azure.com/
AZURE_DOCUMENT_INTELLIGENCE_KEY=<key-vault-secret>
```

The backend uses the `prebuilt-layout` model.

## Azure OpenAI / Azure AI Foundry

```text
DOCUMENT_CLASSIFICATION_BACKEND=azure
CLASSIFICATION_TEXT_MAX_CHARS=6000
CLASSIFICATION_LOW_CONFIDENCE_THRESHOLD=0.65
RULE_BASED_CLASSIFICATION_MIN_CONFIDENCE=0.88

STRUCTURED_EXTRACTION_BACKEND=azure
STRUCTURED_EXTRACTION_TEXT_MAX_CHARS=12000

DOCUMENT_SUMMARIZATION_BACKEND=azure
DOCUMENT_SUMMARY_TEXT_MAX_CHARS=12000
DOCUMENT_SUMMARY_LOW_CONFIDENCE_THRESHOLD=0.5

RUBRIC_SCORING_BACKEND=azure
RUBRIC_SCORING_TEXT_MAX_CHARS=16000
RUBRIC_SCORING_LOW_CONFIDENCE_THRESHOLD=0.65
PARSER_MODE=azure

AZURE_OPENAI_ENDPOINT=https://<resource-name>.openai.azure.com/
AZURE_OPENAI_API_KEY=<key-vault-secret>
AZURE_OPENAI_API_VERSION=<api-version>
AZURE_OPENAI_CLASSIFICATION_MODEL_DEPLOYMENT=<classification-deployment-name>
AZURE_OPENAI_STRUCTURED_EXTRACTION_MODEL_DEPLOYMENT=<structured-extraction-deployment-name>
AZURE_OPENAI_SUMMARIZATION_MODEL_DEPLOYMENT=<summarization-deployment-name>
AZURE_OPENAI_RUBRIC_SCORING_MODEL_DEPLOYMENT=<rubric-scoring-deployment-name>
AZURE_OPENAI_REVIEW_MODEL_DEPLOYMENT=<review-deployment-name>
AZURE_AI_FOUNDRY_PROJECT_ENDPOINT=https://<foundry-project-endpoint>
```

Optional agent metadata:

```text
AZURE_AI_AGENT_DOCUMENT_CLASSIFICATION_NAME=
AZURE_AI_AGENT_DOCUMENT_CLASSIFICATION_VERSION=
AZURE_AI_AGENT_DOCUMENT_CLASSIFICATION_MODEL=
AZURE_AI_AGENT_TRANSCRIPT_EXTRACTION_NAME=
AZURE_AI_AGENT_TRANSCRIPT_EXTRACTION_VERSION=
AZURE_AI_AGENT_TRANSCRIPT_EXTRACTION_MODEL=
AZURE_AI_AGENT_RECOMMENDATION_EXTRACTION_NAME=
AZURE_AI_AGENT_RECOMMENDATION_EXTRACTION_VERSION=
AZURE_AI_AGENT_RECOMMENDATION_EXTRACTION_MODEL=
AZURE_AI_AGENT_ESSAY_EXTRACTION_NAME=
AZURE_AI_AGENT_ESSAY_EXTRACTION_VERSION=
AZURE_AI_AGENT_ESSAY_EXTRACTION_MODEL=
```

## PII Redaction

PII redaction is currently an interface for future Azure AI Language integration:

```text
PII_REDACTION_BACKEND=none
AZURE_LANGUAGE_ENDPOINT=
AZURE_LANGUAGE_KEY=
```

When Azure Language redaction is implemented, store the key in Key Vault and set `PII_REDACTION_BACKEND=azure_language`.

## Observability

```text
APPLICATIONINSIGHTS_CONNECTION_STRING=<key-vault-secret-or-app-setting-reference>
```

Application Insights should collect request logs, exceptions, dependency telemetry, and availability checks. Do not log secrets or full document text.
