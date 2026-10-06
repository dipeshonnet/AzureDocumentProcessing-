# Cloudflare Pages + Render + Supabase + LlamaParse

The selected deployment uses Free plans only. Do not add a payment method,
upgrade a plan, enable paid inference, or enable Cloudflare R2. The API stops
at startup if the `render-free` settings are incompatible with this profile.

## Deployment addresses

- Frontend: `https://admissions-everydayai.pages.dev`
- Backend: `https://admissions-everydayai-api.onrender.com`
- Custom domain: `https://admissions.everydayai.work`
- Render service: `srv-db25pp3bc2fs73fbpfr0`

The backend, Pages frontend and custom domain are live. Cloudflare reports the
domain as Active with SSL enabled, and HTTPS serves the app successfully. Health,
both CORS origins and static security headers passed checks. Protected routes
reject unauthenticated requests.
Authenticated private-upload and live OCR verification passed on October 6, 2026.
The fictional three-page `deployment-smoke.pdf` completed, and the review screen
showed the expected text from pages 1, 2 and 3. The original is in the private
`admissions-raw` bucket; an unauthenticated public object request was denied.
LlamaParse usage showed one Cost Effective job, three pages and nine Free credits
used, with 9,991 credits remaining. This verifies OCR and storage, not the accuracy
of the labeled demo classification, summaries or scores. The synthetic record is
identified by student ID `FREE-DEPLOY-SMOKE-001` and is not a real application.

## Backend

Deploy the public Git repository `dipeshonnet/AzureDocumentProcessing-`, branch
`codex/free-cloudflare-render`, as one Render **Free Web Service** named
`admissions-everydayai-api` in Singapore.
Use Python 3.12.14, `pip install -r requirements-free.txt`, and:

```text
python -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port $PORT --workers 1
```

Health path: `/health`. Render auto-deploys on commits pushed to
`codex/free-cloudflare-render`. No persistent disk, background worker, Render database,
or paid preview environment. `render.yaml` contains all settings.
Enter these privately in Render Environment, never in Git or chat:

| Setting | Value |
| --- | --- |
| DATABASE_URL | `postgresql+psycopg://postgres.eqqwjzrnkvbznjmhsdta:<percent-encoded-password>@aws-0-ap-southeast-2.pooler.supabase.com:5432/postgres?sslmode=require` |
| SUPABASE_SERVICE_KEY | Existing project server-only secret/service_role key |
| LLAMA_CLOUD_API_KEY | Existing LlamaParse Free project key |
| BOOTSTRAP_ADMIN_EMAIL | Your chosen administrator login |
| BOOTSTRAP_ADMIN_PASSWORD | New unique password, at least 16 characters |
| BACKEND_CORS_ORIGINS | `https://admissions.everydayai.work,https://admissions-everydayai.pages.dev` |

Use Supabase project **AdmissionAnalyzer** (`eqqwjzrnkvbznjmhsdta`) in EverydayAI.
The app creates its tables in the private `admissions_app` schema, revokes browser
roles' schema access, and never falls back to `public`. Keep this schema out of
Supabase's exposed API schemas. Use **session pooler port 5432**, not transaction
pooler 6543, because the app uses a persistent database search path.

Create a **private** Supabase Storage bucket `admissions-raw`, maximum file size
4 MB. The server key stays in Render; the frontend gets only the API URL.

## Frontend and domain

### Automatic deployments from GitHub

Production source: `dipeshonnet/AzureDocumentProcessing-`, branch
`codex/free-cloudflare-render`. Push or merge changes into this branch to update
the API through Render and the frontend through `.github/workflows/deploy-cloudflare-pages.yml`.
Pushes to `main` do not deploy this hosting stack.

The workflow installs locked npm dependencies, runs frontend tests, builds with
`VITE_API_BASE_URL=https://admissions-everydayai-api.onrender.com`, and uploads
`frontend/dist` to the existing `admissions-everydayai` Pages project. The explicit
Wrangler `--branch=main` selects the Pages production environment; the GitHub
production source branch is still `codex/free-cloudflare-render`.

Add these **GitHub Actions repository secrets** once:

- `CLOUDFLARE_ACCOUNT_ID`: the account owning the Pages project.
- `CLOUDFLARE_API_TOKEN`: a token with Account / Cloudflare Pages / Edit, scoped
  to that account. Never commit the token or add it to frontend environment files.

The frontend workflow cannot publish until those secrets are configured. Inspect
GitHub Actions for frontend failures and Render Events for backend failures.
Deployments complete independently. Supabase database and private objects persist;
application schema changes still need compatible migration planning.

Cloudflare Direct Upload projects support CI uploads without a native Git connection:
[Cloudflare CI guide](https://developers.cloudflare.com/pages/how-to/use-direct-upload-with-continuous-integration/).

### Manual deployment fallback

The Cloudflare Pages **Direct Upload** project `admissions-everydayai` is created.
Its default domain is `admissions-everydayai.pages.dev`. Build against the actual
Render service URL:

```powershell
.\scripts\build-cloudflare-pages.ps1 -ApiUrl https://admissions-everydayai-api.onrender.com
```

Portable Node/npm installations can pass `-NodePath` and `-NpmCliPath`.
Upload `.tools/cloudflare-pages.zip`. Verify the default Pages domain before
adding `admissions.everydayai.work` through Pages > Custom domains. Cloudflare
should create the DNS binding for the existing `everydayai.work` zone. Inspect any
existing `admissions` DNS record before replacing it; leave other records alone.
No Pages Functions are required. Hash routing needs no SPA rewrite configuration.

## Verification and operational limits

```powershell
.venv\Scripts\python.exe scripts/smoke-azure-mvp.py --help
```

Use the smoke script with the real API/frontend URLs, `--database-dialect postgresql`,
and a privately entered administrator password. Verify database readiness, login,
private upload, tenant permissions, case workflow, and logout. For a live OCR
check use only a fictional three-page fixture and confirm all three pages appear.
Enable external processing approval for the fictional institution first.

LlamaParse uses the cost-effective tier with no automatic SDK retries or paid
fallback. Provider uploads are deleted after parsing where possible. If Render
interrupts a processing job, it becomes failed and requires a deliberate retry;
check the provider credit usage first, because an earlier submission may already
have consumed credits. Queued jobs and completed OCR persist in Supabase.

Render Free sleeps when idle and has ephemeral local storage. Supabase Free can
pause inactive projects. Render hours are shared across services; this workspace
already contains an unrelated `qcc-api` service. Do not use synthetic keep-alive
traffic. Monitor shared usage and allow services to suspend when quotas run out.
Manual invitation links work without SMTP. Classification, summaries, extracted
fields and rubric scores use labeled demo backends; OCR alone is live.

Provider references: [Render Free](https://render.com/docs/free),
[Pages limits](https://developers.cloudflare.com/pages/platform/limits/),
[Supabase connections](https://supabase.com/docs/guides/database/connecting-to-postgres),
[LlamaParse pricing](https://developers.llamaindex.ai/llamaparse/general/pricing/).
