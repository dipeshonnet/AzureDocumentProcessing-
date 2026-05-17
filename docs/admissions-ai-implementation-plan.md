# Admissions AI Implementation Plan

## Current Repository Assessment

The repository is currently minimal. It contains a `.env` file with Azure AI Foundry-oriented settings, but no application source code, package manifest, test configuration, deployment files, or Git metadata were found during inspection.

Observed environment variable names:

- `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`
- `AZURE_AI_AGENT_DOCUMENT_CLASSIFICATION_NAME`
- `AZURE_AI_AGENT_DOCUMENT_CLASSIFICATION_VERSION`
- `AZURE_AI_AGENT_DOCUMENT_CLASSIFICATION_MODEL`
- `AZURE_AI_AGENT_DOCUMENT_EXTRACTION_NAME`
- `AZURE_AI_AGENT_DOCUMENT_EXTRACTION_VERSION`
- `AZURE_AI_AGENT_DOCUMENT_EXTRACTION_MODEL`

The extraction agent variables appear multiple times in the existing `.env`. The implementation should normalize these into distinct, purpose-specific names before runtime validation is added.

## Recommended Implementation Path

Because no working stack is present, use the requested MVP stack:

- Backend: FastAPI
- Frontend: React + TypeScript
- Local database: SQLite
- Production-compatible data layer: SQLAlchemy models and Alembic migrations designed to run on PostgreSQL
- File storage: Azure Blob Storage
- Document parsing: Azure AI Document Intelligence
- AI reasoning: Azure OpenAI or Azure AI Foundry model deployments
- Async processing: FastAPI background tasks for MVP, with an upgrade path to Azure Queue Storage, Azure Functions, or Celery/RQ
- Deployment target: Azure App Service or Azure Container Apps, with Azure Static Web Apps or the backend serving the built frontend

The system must provide reviewer decision support only. It must never automatically admit, reject, waitlist, rank as final, or otherwise make a binding admissions decision.

## Target Architecture

The MVP should be split into a browser-based reviewer interface, a FastAPI application API, a relational database, Azure Blob Storage, Azure AI Document Intelligence, and an Azure-hosted LLM deployment.

High-level flow:

1. A staff user creates or opens an applicant record.
2. The user uploads admissions documents such as transcripts, recommendations, essays, test reports, identity documents, or financial materials.
3. The backend stores the original files in Azure Blob Storage and records metadata in the database.
4. A processing job sends each file to Azure AI Document Intelligence for OCR, layout analysis, and structured extraction.
5. A classification step assigns each uploaded document to an expected document type.
6. An extraction step maps document contents into normalized fields with confidence scores and source citations.
7. An AI review-support step summarizes evidence, flags missing or inconsistent information, and maps evidence to configurable rubric criteria.
8. A human reviewer inspects source documents, extracted fields, confidence levels, and AI-generated notes.
9. The human reviewer records their own decision, comments, and audit trail entries.

The AI output should be explicitly labeled as non-binding assistance. UI copy, API schemas, and database fields should reinforce that a reviewer is responsible for the final outcome.

## Proposed Folder Structure

```text
OnlyAzure/
  backend/
    app/
      api/
        routes/
          applicants.py
          documents.py
          reviews.py
          processing.py
          health.py
      core/
        config.py
        security.py
        logging.py
      db/
        base.py
        session.py
        models.py
        migrations/
      services/
        blob_storage.py
        document_intelligence.py
        ai_foundry.py
        admissions_analysis.py
        audit.py
      schemas/
        applicants.py
        documents.py
        reviews.py
        processing.py
      tests/
        unit/
        integration/
      main.py
    alembic.ini
    pyproject.toml
  frontend/
    src/
      api/
      components/
      features/
        applicants/
        documents/
        review/
      routes/
      styles/
      test/
      main.tsx
    index.html
    package.json
    tsconfig.json
    vite.config.ts
  docs/
    admissions-ai-implementation-plan.md
  infra/
    bicep/
    terraform/
  scripts/
  .env.example
  README.md
```

## Environment Variables

Create a checked-in `.env.example` during implementation and keep real secrets only in local `.env`, Azure Key Vault, or deployment platform secrets.

Recommended backend variables:

```text
APP_ENV=local
APP_SECRET_KEY=
BACKEND_CORS_ORIGINS=http://localhost:5173

DATABASE_URL=sqlite:///./admissions_ai.db

AZURE_STORAGE_ACCOUNT_NAME=
AZURE_STORAGE_CONTAINER_DOCUMENTS=
AZURE_STORAGE_CONNECTION_STRING=

AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT=
AZURE_DOCUMENT_INTELLIGENCE_KEY=

AZURE_AI_FOUNDRY_PROJECT_ENDPOINT=
AZURE_OPENAI_ENDPOINT=
AZURE_OPENAI_API_KEY=
AZURE_OPENAI_API_VERSION=
AZURE_OPENAI_REVIEW_MODEL_DEPLOYMENT=

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

REQUIRE_HUMAN_FINAL_DECISION=true
AI_DECISION_SUPPORT_ONLY=true
```

Recommended frontend variables:

```text
VITE_API_BASE_URL=http://localhost:8000
VITE_APP_NAME=Admissions AI Reviewer
```

For production, prefer managed identity where supported instead of long-lived connection strings or keys.

## Data Model

Use SQLAlchemy models with migrations. Keep the schema PostgreSQL-compatible even when running SQLite locally.

Core tables:

- `users`: reviewer accounts, roles, identity provider subject, status, timestamps.
- `applicants`: applicant profile, program, term, external reference ID, status, timestamps.
- `applications`: application-level metadata, program, cycle, submission status, assigned reviewer.
- `documents`: uploaded document metadata, applicant/application ID, blob URI, original filename, content type, size, checksum, upload status.
- `document_pages`: OCR page-level metadata, page numbers, dimensions, extracted text references.
- `document_classifications`: document type predictions, confidence, model/agent version, reviewer override.
- `extracted_fields`: normalized fields, value, confidence, source page, source text span, extraction model/agent version.
- `processing_jobs`: job type, status, retry count, errors, started/completed timestamps.
- `review_summaries`: AI-generated summaries, evidence citations, caveats, model version, prompt version.
- `rubrics`: configurable review criteria and scoring guidance.
- `rubric_assessments`: AI-assisted evidence mapping and optional reviewer-entered scores.
- `human_reviews`: reviewer notes, final human-only decision recommendation, reviewer ID, timestamps.
- `audit_events`: immutable record of uploads, processing runs, user actions, AI outputs, overrides, and final review submissions.

Implemented MVP core model:

- The current backend foundation implements the applicant, application, document, extracted content, document summary, rubric criterion score, and rubric scorecard tables first.
- `users`, `rubrics`, `human_reviews`, and `audit_events` remain planned follow-up tables.
- Local database setup currently uses SQLAlchemy `create_all` for SQLite development. Alembic migrations should be introduced before production schema changes begin.
- JSON columns are used for OCR pages, tables, key-value pairs, extraction metadata, evidence, section summaries, and reviewer override data.
- Document classification metadata is persisted on application document records, including rationale, evidence snippets, classifier source, and whether human review is required.
- The default admissions rubric is implemented as YAML at `config/rubrics/default_admissions_rubric.yaml` and exposed read-only through `GET /api/v1/rubrics/default`.

Important modeling rules:

- AI-generated content must be stored separately from human-entered review conclusions.
- AI outputs must include model deployment, prompt version, timestamp, and source document references.
- Any final decision field must be writable only by a human reviewer role.
- Store confidence scores and extraction provenance for every normalized field.

## API Endpoints

Initial FastAPI endpoints:

```text
GET    /api/health

POST   /api/applicants
GET    /api/applicants
GET    /api/applicants/{applicant_id}
PATCH  /api/applicants/{applicant_id}

POST   /api/applications
GET    /api/applications/{application_id}
GET    /api/applications/{application_id}/timeline

POST   /api/applications/{application_id}/documents
GET    /api/applications/{application_id}/documents
GET    /api/documents/{document_id}
GET    /api/documents/{document_id}/download-url
POST   /api/documents/{document_id}/process

GET    /api/processing-jobs/{job_id}
POST   /api/applications/{application_id}/process

GET    /api/applications/{application_id}/extracted-fields
PATCH  /api/extracted-fields/{field_id}/review

GET    /api/applications/{application_id}/ai-review-summary
POST   /api/applications/{application_id}/ai-review-summary/regenerate

GET    /api/rubrics
POST   /api/rubrics
POST   /api/applications/{application_id}/rubric-assessments

POST   /api/applications/{application_id}/human-reviews
GET    /api/applications/{application_id}/human-reviews

GET    /api/audit/applications/{application_id}
```

Implemented rubric endpoint:

```text
GET    /api/v1/rubrics/default
```

Implemented scoring endpoint:

```text
POST   /api/v1/applications/{application_id}/score
```

Implemented full pipeline endpoints:

```text
POST   /api/v1/applications/{application_id}/process
GET    /api/v1/applications/{application_id}/processing-status
```

Implemented reviewer endpoints:

```text
GET    /api/v1/review/applications
GET    /api/v1/review/applications/{application_id}
POST   /api/v1/review/applications/{application_id}/criterion/{criterion_id}/override
POST   /api/v1/review/applications/{application_id}/decision
POST   /api/v1/review/applications/{application_id}/notes
```

Endpoint safeguards:

- No endpoint should expose `auto_admit`, `auto_reject`, or equivalent behavior.
- AI summary generation endpoints should return decision-support fields only.
- Human review submission should require reviewer authentication and explicit confirmation that source evidence was reviewed.
- Rubric scoring is advisory only, scores one criterion at a time, ignores protected attributes, and calculates weighted totals deterministically in application code.

## Processing Workflow

1. Upload:
   - Validate file type, size, and malware scanning hook if available.
   - Store original document in Azure Blob Storage.
   - Save metadata and checksum in the database.

2. OCR and layout:
   - Submit document to Azure AI Document Intelligence.
   - Store extracted text, page structure, tables, and confidence metadata.
   - Preserve links from extracted text back to page numbers and spans.

3. Classification:
   - Classify document type using a two-stage process: rule-based filename/OCR checks first, then Azure OpenAI structured-output classification for ambiguous documents.
   - Send only minimal OCR context to the model: filename, detected headings, and a bounded first-page excerpt.
   - Save predicted type, confidence, and rationale.
   - Allow reviewer override.

4. Structured extraction:
   - Run type-specific extraction for transcripts, essays, recommendation letters, identity documents, and other supported document classes.
   - Output typed JSON validated by Pydantic schemas.
   - Store each extracted field with confidence and citation.
   - Implemented document-specific schemas now cover transcripts, personal statements, recommendation letters, resumes/CVs, and test score reports.
   - The structured extractor stores validated JSON on the OCR content record and requires evidence or an uncertainty explanation for important fields.

5. Completeness and consistency checks:
   - Check required documents by program and admissions cycle.
   - Flag missing pages, unreadable sections, inconsistent names, mismatched dates, or conflicting academic records.
   - Mark issues as review flags, not decisions.

6. AI review-support summary:
   - Summarize applicant materials against configured rubric criteria.
   - Include strengths, concerns, missing information, and source references.
   - Avoid protected-class inference and avoid speculative conclusions.
   - Include a clear disclaimer that the content is decision support only.
   - Implemented document-level summaries now store short summary, section summaries, strengths, concerns, missing information, reviewer attention points, and evidence on `document_summaries`.
   - Low-confidence or unreadable OCR produces an explicit caution summary instead of inferred content.

7. Human review:
   - Reviewer inspects documents, extracted fields, AI summary, and flags.
   - Reviewer edits or accepts extracted values where appropriate.
   - Reviewer records comments and a human-only recommendation or decision according to institution policy.

8. Audit:
   - Log every upload, AI run, prompt/model version, extraction change, reviewer override, and final human action.

Implemented application pipeline:

- The `ApplicationProcessingService` validates the application and uploaded documents, then runs OCR extraction, document classification, structured extraction, document summarization, and advisory rubric scoring.
- Application-level processing statuses are `pending`, `extracting`, `classifying`, `summarizing`, `scoring`, `ready_for_review`, `failed`, and `needs_manual_review`.
- Each step is idempotent by default and reuses persisted output unless `force=true` is passed.
- Structured error records are stored on the application with step, document ID, error type, safe message, and blocking status.
- Document-level failures are non-blocking when at least one other document produces usable OCR evidence. Scoring is blocked if no usable evidence exists or the scoring step itself fails.
- The generated scorecard is reviewer decision support only. It never auto-admits, auto-rejects, waitlists, or records a final admissions decision.

Implemented reviewer workflow:

- Reviewer detail responses assemble applicant metadata, document classifications, summaries, structured fields, rubric scores, evidence, confidence, human-review flags, model versions, prompt versions, reviewer overrides, audit metadata, and the final human decision.
- Criterion overrides are stored separately from AI-generated scores and require a human-entered reason.
- Final human decisions are stored on the application record, not on the AI scorecard or recommendation band.
- Allowed final human decision values are `admit`, `waitlist`, `reject`, `needs_more_information`, and `defer`.
- Reviewer actions append audit metadata that marks AI output as decision support only.

Implemented reviewer UI:

- A React + TypeScript reviewer app now lives under `frontend/`.
- The UI includes application list, application detail, document detail, rubric scorecard, and reviewer decision panel views.
- The application list shows applicant name, program, status, weighted score, human-review requirement, and submitted date.
- Detail views show document classifications, confidence, summaries, extracted fields, evidence snippets, rubric scores, missing documents, and human-review flags.
- Reviewers can submit criterion overrides with required reasons, save notes, set final human decisions, and mark an application as needing more information.
- The UI displays `Decision support only`, highlights low-confidence or uncertain fields, and does not present AI output as an automatic admit or reject decision.

Implemented admissions operations console:

- The frontend now opens as the `Admission Analyser` operations console with local login/registration, a sticky desktop sidebar, compact mobile navigation, and pages for Intake, Integrations, Rubrics, Users, and Profile.
- Local auth seeds the demo admin account `superadmin` / `EverydayAI` and protects the intake console with bearer-token sessions.
- The Intake page uploads selected files sequentially with `XMLHttpRequest`, shows real upload progress, keeps completed and failed cards visible, and polls `/api/jobs/status` for parser status.
- The backend exposes `/api/upload`, `/api/jobs/status`, `/api/jobs/{job_id}`, and `/api/jobs/{job_id}/record`, backed by the existing applicant, application, document, storage, and audit models plus an `IntakeJob` wrapper.
- The local parser worker uses one in-process `asyncio.Queue`, requeues interrupted jobs on startup, and defaults to `PARSER_MODE=mock` for deterministic extracted admissions records.
- Rubric management now supports multiple saved operational rubrics in the local database. Each rubric can contain sections, sub-components, and row-level scoring rules such as GPA ranges, prerequisite status thresholds, recommendation letter categories, and short-answer rubric rows. The Intake upload flow validates and stores the selected rubric ID with the intake job.

Implemented prompt versioning:

- LLM prompts now live as versioned markdown templates under `prompts/`.
- Each prompt includes `prompt_name`, `prompt_version`, `input_schema`, `output_schema`, and `safety_constraints` metadata.
- Classification, structured extraction, summarization, and rubric scoring render prompt templates instead of inline prompt strings.
- Prompt name and version are persisted with generated classification, structured extraction, summary, and rubric scoring metadata.
- Historical prompt versions must remain in the repository for auditability; prompt behavior changes should be introduced as new versions.

## Azure Resources Required

Recommended Azure resources:

- Azure Storage Account with private container for admissions documents.
- Azure AI Document Intelligence resource.
- Azure OpenAI resource or Azure AI Foundry project with model deployments.
- Azure Key Vault for secrets.
- Azure App Service or Azure Container Apps for the FastAPI backend.
- Azure Static Web Apps or App Service static hosting for the React frontend.
- Azure Database for PostgreSQL for production.
- Application Insights and Log Analytics for observability.
- Microsoft Entra ID app registration for staff authentication.
- Optional Azure Queue Storage or Service Bus for durable processing jobs.
- Optional Azure Functions or Container Apps jobs for background processors.
- Optional Defender for Cloud or malware scanning integration for uploaded files.

Networking and access:

- Use managed identity from the backend to Blob Storage, Key Vault, and supported Azure AI resources.
- Restrict Blob Storage public access.
- Prefer private endpoints for production environments handling sensitive admissions records.
- Store only short-lived signed URLs when document preview/download is required.

## Local Development Instructions

Initial local workflow after scaffolding:

1. Install backend dependencies:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

2. Configure environment:

```powershell
Copy-Item ..\.env.example ..\.env
```

3. Run database migrations:

```powershell
alembic upgrade head
```

4. Start the backend:

```powershell
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

5. Install frontend dependencies:

```powershell
cd frontend
npm install
```

6. Start the frontend:

```powershell
npm run dev
```

7. Open the reviewer UI:

```text
http://localhost:5173
```

Package manager recommendation:

- Use `pip` or `uv` for Python. If choosing `uv`, commit `uv.lock`.
- Use `npm` for the React frontend unless the team already prefers `pnpm`.

## Testing Strategy

Backend:

- Use `pytest` for unit and integration tests.
- Use FastAPI `TestClient` or `httpx` for API tests.
- Mock Azure Blob Storage, Document Intelligence, and Azure OpenAI/Foundry clients in unit tests.
- Add contract tests for Pydantic extraction schemas.
- Add migration tests for SQLite and PostgreSQL compatibility.
- Add security tests for role checks on human-review endpoints.

Frontend:

- Use Vitest and React Testing Library for component and feature tests.
- Use Playwright for end-to-end reviewer workflows after the UI exists.
- Test upload states, extraction confidence rendering, reviewer overrides, and final human review submission.

AI and document workflow:

- Maintain a small synthetic fixture set of non-sensitive admissions-like documents.
- Test classification and extraction parsing against deterministic JSON schemas.
- Evaluate summary outputs for citation coverage, refusal to make final decisions, and absence of protected-class inference.
- Version prompts and keep regression tests for known document examples.
- Run the `evals/` harness against synthetic application packets for strong, average, missing-document, weak-evidence, unreadable, ambiguous, and protected-attribute scenarios.
- Keep mock evals as the default local and CI path; require explicit opt-in before running real Azure OpenAI evals.

Manual QA:

- Verify that low-confidence fields are visually distinct.
- Verify that source citations open the correct document/page.
- Verify that reviewers can override AI output.
- Verify that no UI path presents an AI output as a final admissions decision.

## Security and Human-Review Safeguards

Required safeguards:

- The system must never auto-admit, auto-reject, auto-waitlist, or make binding admissions decisions.
- AI outputs must be labeled as reviewer decision support.
- Final decision or recommendation fields must require authenticated human reviewer action.
- Role concepts are `admin`, `admissions_reviewer`, and `read_only_auditor`; local development uses replaceable `X-Dev-Actor` and `X-Dev-Role` headers until production identity integration is added.
- Durable audit logs capture actor, action, timestamp, application ID, document ID when applicable, and sanitized old/new values for uploads, AI processing steps, score overrides, and final decisions.
- AI summaries must cite source documents and distinguish evidence from inference.
- Low-confidence extraction must be flagged for human review.
- Reviewers must be able to correct extracted fields and document classifications.
- Store immutable audit events for AI runs, reviewer edits, and final submissions.
- Keep secrets and full document text out of application logs and audit records.
- Use the PII redaction service interface before future Azure AI Language integration points that need redacted text variants.
- Do not infer or score protected characteristics.
- Do not use applicant demographic data unless explicitly required by a lawful, institution-approved process.
- Encrypt documents at rest and in transit.
- Use least-privilege access for Azure resources.
- Keep secrets out of source control and rotate any exposed credentials.
- Use short-lived document access URLs.
- Add retention and deletion policies aligned with institutional and legal requirements.
- Include prompt-injection defenses for document content by treating uploaded text as untrusted data.
- Require model outputs to follow strict JSON schemas for extraction tasks.
- Show reviewers the original document evidence alongside AI-generated summaries.

Recommended UI language:

```text
AI-generated review support. This content is not an admissions decision and must be verified by an authorized human reviewer.
```

## Deployment Structure

Recommended deployment artifacts:

- `backend/Dockerfile` for the FastAPI app.
- `docker-compose.yml` for local backend, frontend, and PostgreSQL development.
- `frontend` static build deployed separately or served by the backend.
- `infra/bicep` or `infra/terraform` for Azure resources.
- GitHub Actions or Azure DevOps pipeline for lint, test, build, and deploy.
- Environment-specific configuration for local, staging, and production.

Suggested environments:

- Local: SQLite, local dev servers, Azure services in a development resource group.
- Staging: Azure Database for PostgreSQL, staging Blob container, staging AI deployments.
- Production: isolated resource group, managed identity, Key Vault, private networking where possible, production AI deployments.

Current deployment documentation:

- `docs/admissions-ai-azure-deployment.md` documents Azure Container Apps or App Service, Blob Storage, Document Intelligence, Azure OpenAI / Foundry deployments, PostgreSQL or Azure SQL, Application Insights, Key Vault, deployment checklist, and manual deployment flow.
- `docs/admissions-ai-production-env.md` lists production environment variables and secret-handling expectations.
- No `.github/workflows` directory exists, so CI is documented through `scripts/ci-test.ps1`, `scripts/ci-test.sh`, and `make test` rather than adding a GitHub Actions workflow.

## Next Implementation Milestones

1. Add `.env.example`, `README.md`, backend package files, and frontend package files.
2. Scaffold FastAPI with health endpoint, configuration loading, logging, and database session setup.
3. Add SQLAlchemy models and Alembic migrations.
4. Scaffold React + TypeScript reviewer UI.
5. Implement document upload to Azure Blob Storage.
6. Integrate Azure AI Document Intelligence for OCR/layout.
7. Add classification and extraction service interfaces.
8. Add AI review-support summaries with citations and safeguards.
9. Add reviewer override and human review flows.
10. Add tests and deployment infrastructure.
