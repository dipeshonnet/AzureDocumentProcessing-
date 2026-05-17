# Admissions AI Final Readiness Checklist

This checklist summarizes the final hardening pass for the admissions AI reviewer workflow. The system remains reviewer decision support only and must not be used to auto-admit, auto-reject, auto-waitlist, or make binding admissions decisions.

## What Works

- FastAPI backend with health, config-check, application, document, rubric, processing, scoring, and reviewer APIs.
- React + TypeScript reviewer UI for application lists, application details, document details, rubric scorecards, overrides, notes, and final human decisions.
- SQLite local MVP support and PostgreSQL-compatible SQLAlchemy models.
- Docker backend image and Docker Compose local development stack with backend, frontend, and PostgreSQL.
- Local storage service with safe upload paths and file type/size validation.
- Azure service boundaries for Blob Storage, Document Intelligence, Azure OpenAI, Azure AI Foundry, and future Azure Language PII redaction.
- Document Intelligence extraction service interface and Azure prebuilt-layout implementation.
- Two-stage document classification with rule-based handling before Azure OpenAI fallback.
- Structured extraction, summarization, and rubric scoring services using strict JSON structured outputs.
- Versioned prompt templates in `prompts/` with prompt metadata.
- Prompt name and version persistence for classification, structured extraction, summaries, and rubric scoring.
- Deterministic weighted score calculation in application code.
- Reviewer final decisions stored separately from AI scorecards.
- Development role model for `admin`, `admissions_reviewer`, and `read_only_auditor`.
- Durable audit logs for upload, extraction, classification, structured extraction, summarization, scoring, score override, and final decision.
- Audit sanitization to avoid storing secrets or full document text.
- Mock eval harness with synthetic cases for strong, average, missing transcript, weak recommendation, ambiguous GPA scale, unreadable document, and protected-attribute scenarios.

## What Remains Manual

- Production authentication and authorization must replace the development-only header middleware.
- Azure Blob upload implementation is still a boundary; local storage is implemented, but Azure upload calls remain manual work.
- PII redaction is an interface only; Azure AI Language integration is not implemented.
- Alembic or equivalent database migrations should be introduced before production.
- Infrastructure as code is documented but not implemented.
- GitHub Actions was not added because this repository does not currently contain `.github/workflows`; CI commands are documented through scripts.
- Production monitoring dashboards, alerts, retention policies, and incident runbooks must be configured by operators.
- Reviewer training and institutional policy approval remain required before use with real admissions records.

## Azure Configuration Required

Required production resources:

- Azure Container Apps or Azure App Service for the backend container.
- Static frontend hosting, such as Azure Static Web Apps or App Service/static storage hosting.
- Azure Blob Storage private container for uploaded documents.
- Azure AI Document Intelligence resource for `prebuilt-layout`.
- Azure OpenAI / Azure AI Foundry deployments for classification, structured extraction, summarization, and rubric scoring.
- Azure Database for PostgreSQL Flexible Server or Azure SQL Database.
- Azure Key Vault for secrets.
- Application Insights for telemetry.

Required production settings are listed in:

```text
docs/admissions-ai-production-env.md
```

Deployment guidance is listed in:

```text
docs/admissions-ai-azure-deployment.md
```

Important production settings:

- `DEV_AUTH_ENABLED=false` after real auth integration.
- `AUTO_CREATE_DB_SCHEMA=false`.
- `REQUIRE_HUMAN_FINAL_DECISION=true`.
- `AI_DECISION_SUPPORT_ONLY=true`.
- CORS must allow only approved frontend origins.
- Secrets must come from Key Vault or managed identity, not checked-in files.

## Production Risks

- The current development auth middleware is not production authentication.
- Azure Blob Storage write support must be completed before `STORAGE_BACKEND=azure` can be used.
- Database migrations are not yet formalized.
- AI output quality depends on prompt/version governance, model deployment stability, eval coverage, and human reviewer discipline.
- OCR quality can materially affect summaries, structured extraction, and rubric scoring.
- Protected-attribute text may appear in source documents; scoring safeguards flag it, but human reviewers must enforce institutional policy.
- Audit logs are durable but not immutable append-only storage; production should consider write-once retention or external audit export.
- Application Insights must be configured carefully to avoid collecting raw document text or secrets.
- Load, concurrency, rate-limit, and large-file performance testing remain to be done.

## Recommended Next Improvements

- Implement Azure Blob Storage uploads with managed identity and least-privilege RBAC.
- Add Alembic migrations and migration tests for PostgreSQL.
- Replace development auth with Entra ID or the institution-approved identity provider.
- Add role-based UI behavior for reviewers and auditors.
- Implement Azure AI Language PII redaction when the target review policy is approved.
- Add CI workflow in the repository once a CI provider is chosen.
- Add OpenTelemetry/Application Insights instrumentation with safe log filters.
- Add end-to-end browser tests for upload, processing, override, and final-decision flows.
- Add human-reviewed golden datasets and expand eval thresholds before production launch.
- Add backup, restore, retention, and deletion procedures for admissions records.

## Final Hardening Findings

- Secret-pattern scan found no suspicious API keys, JWTs, Netlify tokens, or private key blocks in repository files excluding ignored local `.env`, generated build output, node modules, caches, and eval results.
- `.env` exists locally and is ignored; do not commit it.
- All Azure OpenAI calls found in backend services use structured `response_format`.
- Weighted application scoring is deterministic code; the model scores only one rubric criterion at a time.
- No code path was found that auto-admits, auto-rejects, or records final human decisions from AI output.
- Missing scoring evidence now adds the `missing_supporting_evidence` risk flag and requires human review.
- Reviewer prompt-version reporting now reads the actual versioned prompt templates.
- Human score overrides and final decisions are recorded in durable audit logs.
- No first-party lint configuration is present; frontend type checking is covered by `npm run build`.
