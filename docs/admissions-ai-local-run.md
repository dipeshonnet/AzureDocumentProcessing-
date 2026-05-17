# Admissions AI Local Run

This repository contains a FastAPI backend under `backend/` and a React + TypeScript admissions operations console under `frontend/`.
Azure-facing integrations are represented as service boundaries and can be mocked in tests until production credentials are configured.

## Prerequisites

- Python 3.11 or newer
- `pip`
- Node.js 20 or newer
- `npm`

## Install Dependencies

From the repository root:

```powershell
python -m pip install -r requirements.txt
```

## Configure Environment

Create a local `.env` from the example file:

```powershell
Copy-Item .env.example .env
```

Fill in local or development Azure values when you are ready to exercise real integrations. The current backend starts without them, and `/api/v1/system/config-check` reports which required values are still missing.

Do not commit real secrets.

For local uploads, keep:

```text
STORAGE_BACKEND=local
LOCAL_STORAGE_ROOT=./storage
MAX_UPLOAD_BYTES=10485760
```

Uploaded files are stored under:

```text
applications/{application_id}/raw/{document_id}/{safe_filename}
```

Allowed upload types are `pdf`, `docx`, `txt`, `jpg`, `jpeg`, and `png`.

## Run The Backend

From the repository root:

```powershell
cd backend
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Health endpoint:

```text
http://127.0.0.1:8000/health
```

Configuration check endpoint:

```text
http://127.0.0.1:8000/api/v1/system/config-check
```

The config-check response only reports whether required variables are configured. It does not return secret values.

## Run The Reviewer UI

Install frontend dependencies:

```powershell
cd frontend
npm install
```

Create a frontend environment file if you need a non-default backend URL:

```powershell
Copy-Item .env.example .env
```

Default frontend setting:

```text
VITE_API_BASE_URL=http://127.0.0.1:8000
```

Start the React development server:

```powershell
npm run dev -- --host 127.0.0.1 --port 5173
```

Open the operations console:

```text
http://127.0.0.1:5173
```

Sign in with the local demo account:

```text
email: superadmin
password: EverydayAI
```

The UI includes Intake, Integrations, Rubrics, Users, and Profile pages. The Intake page uploads documents through `/api/upload`, polls `/api/jobs/status`, and displays parsed admissions records from the local mock parser by default. It displays `Decision support only`, confidence/evidence context, failed upload/parser messages, and never labels an applicant as automatically accepted or rejected.

Rubrics are editable from the Rubrics page. The local database stores multiple saved rubrics with sections, sub-components, and row-level scoring rules. Saved rubrics immediately appear in the Intake page rubric dropdown so staff can select the applicable rubric before uploading applicant documents. The seeded operations rubric includes GPA, prerequisites, letters of recommendation, short answer, experience, and socioeconomic context sections based on the current local template.

## Intake Operations API

Development session endpoints:

```text
POST /api/auth/login
POST /api/auth/register
POST /api/auth/logout
GET  /api/auth/me
```

Intake endpoints:

```text
POST /api/upload
GET  /api/jobs/status
GET  /api/jobs/{job_id}
GET  /api/jobs/{job_id}/record
GET  /api/rubrics
GET  /api/rubrics/{rubric_id}
POST /api/rubrics
PUT  /api/rubrics/{rubric_id}
```

`POST /api/upload` accepts one file per request plus applicant metadata, creates or reuses the admissions applicant/application records, stores the file with the existing path pattern, creates an intake job, and queues it for the in-process worker. The frontend uploads selected files sequentially with `XMLHttpRequest` so upload progress is real.

`POST /api/upload` also validates the selected `rubric_id`. If the rubric is missing, the upload is rejected before creating the job.

Local parsing defaults:

```text
PARSER_MODE=mock
INTAKE_WORKER_ENABLED=true
INTAKE_MOCK_PROCESSING_DELAY_SECONDS=0.15
```

The in-process worker runs one queue sequentially. On startup it requeues queued jobs and resets interrupted processing jobs with `Processing was interrupted. Waiting to restart.` No Redis, Celery, BullMQ, or external queue is required for the local MVP.

## Application And Document Endpoints

Create an application for an existing applicant:

```text
POST /api/v1/applications
```

Upload a document:

```text
POST /api/v1/applications/{application_id}/documents
```

Read an application:

```text
GET /api/v1/applications/{application_id}
```

List uploaded documents:

```text
GET /api/v1/applications/{application_id}/documents
```

The upload endpoint stores bytes and metadata only. It does not call Azure AI Document Intelligence.

## Document Extraction

The extraction endpoint runs OCR/layout extraction for an uploaded document:

```text
POST /api/v1/documents/{document_id}/extract
```

By default, local development uses the mock extractor:

```text
DOCUMENT_EXTRACTION_BACKEND=mock
```

To use Azure AI Document Intelligence, install dependencies from `requirements.txt` and configure:

```text
DOCUMENT_EXTRACTION_BACKEND=azure
AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT=https://<your-resource-name>.cognitiveservices.azure.com/
AZURE_DOCUMENT_INTELLIGENCE_KEY=<your-key>
DOCUMENT_EXTRACTION_TIMEOUT_SECONDS=120
```

The Azure implementation uses the `prebuilt-layout` model and persists raw text, page-level text, tables, key-value pairs where available, page count, and confidence metadata. It does not run LLM classification or scoring.

The endpoint is idempotent. If extraction already exists, it returns the existing extraction. To re-run extraction:

```text
POST /api/v1/documents/{document_id}/extract?force=true
```

Do not log document content or Azure keys. Keep real Azure keys in local `.env`, Azure Key Vault, or deployment platform secrets only.

## Document Type Classification

The classification endpoint assigns one of the supported document types to an uploaded document:

```text
POST /api/v1/documents/{document_id}/classify
```

Supported types:

```text
personal_statement
transcript
recommendation_letter
resume_cv
test_score_report
application_form
other_or_unreadable
```

Classification uses a two-stage design:

1. Rule-based and metadata-assisted classification for obvious filenames, headings, and OCR text.
2. Azure OpenAI structured-output classification for ambiguous documents.

Local development can use the mock classifier:

```text
DOCUMENT_CLASSIFICATION_BACKEND=mock
CLASSIFICATION_TEXT_MAX_CHARS=6000
CLASSIFICATION_LOW_CONFIDENCE_THRESHOLD=0.65
RULE_BASED_CLASSIFICATION_MIN_CONFIDENCE=0.88
```

To use Azure OpenAI:

```text
DOCUMENT_CLASSIFICATION_BACKEND=azure
AZURE_OPENAI_ENDPOINT=https://<your-resource-name>.openai.azure.com/
AZURE_OPENAI_API_KEY=<your-key>
AZURE_OPENAI_API_VERSION=2024-10-21
AZURE_OPENAI_CLASSIFICATION_MODEL_DEPLOYMENT=<your-classifier-deployment-name>
```

The Azure classifier requests strict JSON with:

```json
{
  "document_type": "transcript",
  "confidence": 0.92,
  "rationale": "Brief reason for the classification.",
  "evidence_snippets": ["short OCR evidence"],
  "requires_human_review": false
}
```

Only minimal OCR context is sent to the model: filename, detected headings, and the first three pages or the first `CLASSIFICATION_TEXT_MAX_CHARS` characters. Classification metadata is persisted with the document, but document content and secrets must not be logged.

## Document-Specific Structured Extraction

After OCR extraction and document classification, run document-specific structured extraction:

```text
POST /api/v1/documents/{document_id}/structured-extract
```

Supported structured extraction schemas:

```text
transcript
personal_statement
recommendation_letter
resume_cv
test_score_report
```

`application_form` and `other_or_unreadable` are intentionally not structured-extracted yet.

Local development can use the mock extractor:

```text
STRUCTURED_EXTRACTION_BACKEND=mock
STRUCTURED_EXTRACTION_TEXT_MAX_CHARS=12000
```

To use Azure OpenAI structured outputs:

```text
STRUCTURED_EXTRACTION_BACKEND=azure
AZURE_OPENAI_ENDPOINT=https://<your-resource-name>.openai.azure.com/
AZURE_OPENAI_API_KEY=<your-key>
AZURE_OPENAI_API_VERSION=2024-10-21
AZURE_OPENAI_STRUCTURED_EXTRACTION_MODEL_DEPLOYMENT=<your-structured-extraction-deployment-name>
```

The structured extractor selects the output schema from the persisted `document_type`, sends bounded OCR text plus detected headings, and validates the returned JSON before persistence. Each important field includes evidence snippets. If a field lacks evidence, it must be marked uncertain with an explanation rather than filled with invented data.

Validation rejects obvious impossible values, including GPA greater than GPA scale, negative scores, and missing recommendation strength without an explanation. Structured extraction output is persisted on the OCR content record under `structured_extraction`.

## Document Summarization

After OCR extraction, generate a grounded document summary:

```text
POST /api/v1/documents/{document_id}/summarize
```

The endpoint is idempotent. If a summary already exists, it returns the existing summary. To regenerate:

```text
POST /api/v1/documents/{document_id}/summarize?force=true
```

Local development can use the mock summarizer:

```text
DOCUMENT_SUMMARIZATION_BACKEND=mock
DOCUMENT_SUMMARY_TEXT_MAX_CHARS=12000
DOCUMENT_SUMMARY_LOW_CONFIDENCE_THRESHOLD=0.5
```

To use Azure OpenAI structured outputs:

```text
DOCUMENT_SUMMARIZATION_BACKEND=azure
AZURE_OPENAI_ENDPOINT=https://<your-resource-name>.openai.azure.com/
AZURE_OPENAI_API_KEY=<your-key>
AZURE_OPENAI_API_VERSION=2024-10-21
AZURE_OPENAI_SUMMARIZATION_MODEL_DEPLOYMENT=<your-summary-deployment-name>
```

The summarizer produces `short_summary`, `section_summaries`, `strengths`, `concerns`, `missing_information`, `reviewer_attention_points`, and `evidence`. It must summarize only extracted OCR text. If OCR is empty or below `DOCUMENT_SUMMARY_LOW_CONFIDENCE_THRESHOLD`, the summary clearly marks the document as unreadable or low-confidence and asks the reviewer to inspect the original document manually.

## Rubric Management

The default admissions rubric lives at:

```text
config/rubrics/default_admissions_rubric.yaml
```

Read it through the API:

```text
GET /api/v1/rubrics/default
```

Admissions staff can safely edit the YAML file by changing criterion descriptions, scoring level text, evidence requirements, human review triggers, and weights. Keep these guardrails:

- Keep all required criteria unless the admissions policy owner approves a rubric revision.
- Keep `criterion_id` values stable because downstream scoring and audit records will reference them.
- Keep weights summing to either `1.0` or `100`.
- Keep `max_score` positive.
- Keep scoring levels `1` through `5` for every criterion.
- Keep evidence requirements and human review triggers specific enough for reviewers to verify.
- Treat the rubric as reviewer decision support only; it must not auto-admit or auto-reject applicants.

After editing the rubric, run:

```powershell
python -m pytest backend/tests/test_rubrics.py
```

The API will reject an invalid default rubric rather than serving unsafe configuration.

## Rubric Scoring

Run advisory rubric scoring for an application:

```text
POST /api/v1/applications/{application_id}/score
```

The endpoint is idempotent. If scores already exist, it returns the persisted scorecard. To regenerate:

```text
POST /api/v1/applications/{application_id}/score?force=true
```

Local development can use the mock scorer:

```text
RUBRIC_SCORING_BACKEND=mock
RUBRIC_SCORING_TEXT_MAX_CHARS=16000
RUBRIC_SCORING_LOW_CONFIDENCE_THRESHOLD=0.65
```

To use Azure OpenAI structured outputs:

```text
RUBRIC_SCORING_BACKEND=azure
AZURE_OPENAI_ENDPOINT=https://<your-resource-name>.openai.azure.com/
AZURE_OPENAI_API_KEY=<your-key>
AZURE_OPENAI_API_VERSION=2024-10-21
AZURE_OPENAI_RUBRIC_SCORING_MODEL_DEPLOYMENT=<your-rubric-scoring-deployment-name>
```

The scorer evaluates one criterion at a time and requires strict JSON with score, rationale, supporting evidence, missing information, confidence, human-review status, and risk flags. Weighted score calculation is deterministic application code, not model output.

Scoring safeguards:

- Scores are decision support only and must not auto-admit or auto-reject applicants.
- The model is instructed not to infer or use protected traits such as race, religion, gender, nationality, disability, or age.
- If protected-attribute-like text appears in evidence, the scorecard is flagged for human review.
- If evidence is insufficient, confidence is lowered, missing information is recorded, and human review is required.
- Recommendation bands are evidence-support labels, not final admissions decisions.

## Full Application Processing Pipeline

Run the full reviewer-support pipeline for an application:

```text
POST /api/v1/applications/{application_id}/process
```

The endpoint validates that the application exists and has uploaded documents, then processes each document in this sequence:

```text
pending -> extracting -> classifying -> summarizing -> scoring -> ready_for_review
```

If any document needs reviewer attention or a non-blocking document step fails, the final status is:

```text
needs_manual_review
```

If scoring cannot safely run, the final status is:

```text
failed
```

Check status without re-running the pipeline:

```text
GET /api/v1/applications/{application_id}/processing-status
```

The process endpoint is idempotent. Existing OCR extraction, classification metadata, structured extraction, summaries, and scorecards are reused by default. To refresh all pipeline outputs:

```text
POST /api/v1/applications/{application_id}/process?force=true
```

Structured processing errors are stored on the application as `processing_errors`. Each record includes the pipeline step, optional document ID, error type, safe message, and whether the failure blocked scoring. The pipeline continues with other documents when an individual document fails extraction, classification, structured extraction, or summarization. It blocks scoring only when no usable OCR evidence is available or rubric scoring itself fails.

The response includes the current application status, document metadata, structured errors, and the reviewer-ready advisory scorecard when scoring succeeds. The scorecard remains decision support only and must not be treated as an admit, reject, or waitlist decision.

## Reviewer APIs

Reviewer-facing endpoints read the processed application packet and store human actions separately from AI outputs:

```text
GET  /api/v1/review/applications
GET  /api/v1/review/applications/{application_id}
POST /api/v1/review/applications/{application_id}/criterion/{criterion_id}/override
POST /api/v1/review/applications/{application_id}/decision
POST /api/v1/review/applications/{application_id}/notes
```

The application detail response includes applicant metadata, documents, classifications, summaries, structured extracted fields, rubric scores, supporting evidence, confidence, human-review flags, model/prompt versions, reviewer overrides, audit metadata, and the final human decision when recorded.

Criterion overrides require a non-empty `reason`. Overrides are stored as reviewer metadata on the criterion score and scorecard; the original AI advisory score is preserved. Final decisions are stored on `final_human_decision` and allowed values are:

```text
admit
waitlist
reject
needs_more_information
defer
```

The decision endpoint requires acknowledgement that the AI recommendation is not final. Audit metadata is recorded for overrides, final decisions, and reviewer notes.

## Prompt Versioning

LLM prompts are versioned markdown templates under:

```text
prompts/
```

Each prompt includes metadata for `prompt_name`, `prompt_version`, `input_schema`, `output_schema`, and `safety_constraints`. The backend persists prompt name and version with classification, structured extraction, summary, and rubric scoring outputs.

Do not edit a prompt version after it has been used for review. Create a new versioned file such as `rubric_scoring.v2.md`, update the relevant service mapping, and keep the old template for auditability. See:

```text
docs/admissions-ai-prompt-versioning.md
```

## Evaluation Harness

Synthetic admissions AI evals live under:

```text
evals/
```

Run the default mock evals from the repository root:

```powershell
python -m evals.run_evals
```

or, when `make` is available:

```powershell
make eval
```

On Windows without `make`:

```powershell
.\scripts\run-evals.ps1
```

The default eval provider is `mock`, so no Azure services are called. Real Azure OpenAI rubric scoring evals require an explicit opt-in:

```powershell
python -m evals.run_evals --provider azure --enable-azure-openai
```

See result interpretation guidance in:

```text
docs/admissions-ai-evals.md
```

## Development Auth, Roles, And Audit Logs

The backend includes a replaceable development-only auth middleware. In local development it accepts optional request headers:

```text
X-Dev-Actor: reviewer-1
X-Dev-Role: admissions_reviewer
```

Allowed roles are:

```text
admin
admissions_reviewer
read_only_auditor
```

When headers are omitted, local requests default to `dev-local-reviewer` with the `admissions_reviewer` role. `read_only_auditor` can read reviewer packets but cannot upload documents, run AI steps, override scores, or record final decisions.

Security-sensitive workflow actions are written to the `audit_logs` table, including document upload, extraction, classification, summarization, scoring, score override, and final human decision. Audit values are sanitized so secrets and full document text are not stored in logs.

PII redaction is currently an interface only:

```text
PII_REDACTION_BACKEND=none
```

`azure_language` is reserved for a future Azure AI Language integration and requires:

```text
AZURE_LANGUAGE_ENDPOINT=
AZURE_LANGUAGE_KEY=
```

## Docker Compose

Local container development can run the backend, frontend, and PostgreSQL:

```powershell
docker compose up --build
```

Services:

```text
backend  http://localhost:8000
frontend http://localhost:5173
db       localhost:5432
```

The compose setup uses mock AI services and local file storage. It sets `AUTO_CREATE_DB_SCHEMA=true` only for local PostgreSQL bootstrap. Production must use migrations and keep `AUTO_CREATE_DB_SCHEMA=false`.

Azure deployment planning docs:

```text
docs/admissions-ai-azure-deployment.md
docs/admissions-ai-production-env.md
```

## Run Tests

From the repository root:

```powershell
python -m pytest backend/tests
```

Frontend tests:

```powershell
cd frontend
npm test
```

Frontend production build:

```powershell
cd frontend
npm run build
```

## Current Safeguards

- The API is initialized as reviewer decision support only.
- Configuration includes `REQUIRE_HUMAN_FINAL_DECISION=true`.
- Configuration includes `AI_DECISION_SUPPORT_ONLY=true`.
- No Azure services are called by the current backend foundation.
