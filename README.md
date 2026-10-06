# OnlyAzure

Production-oriented admissions review app.
Backend, frontend, prompts, evals, and docs live here.

Deployed pilot: [admissions.everydayai.work](https://admissions.everydayai.work).
Uses Cloudflare Pages Free, Render Free, Supabase Free and LlamaParse Free.

Use the **Cases** workspace for program checklists, source verification, reviewer
sign-off, CSV exchange and commercial reporting. See the [case workflow guide](docs/admissions-case-workflows.md)
for setup, role access and database upgrade instructions.

Start with `docs/admissions-ai-local-run.md`.
Use `docker-compose.yml` for full-stack runs.

For the selected Free deployment at `admissions.everydayai.work`, follow the
[Cloudflare Pages + Render guide](docs/cloudflare-render-free.md). Supabase stores
the database and private documents; LlamaParse provides OCR. Other AI output uses
clearly labeled demo backends.

For the previously considered free Azure pilot, with a static frontend,
F1 backend, free-offer SQL and LlamaParse OCR, follow [the deployment guide](docs/azure-free-tier-mvp.md).
The free-only profile uses App Service F1, Azure SQL's free offer, Supabase Free private storage,
and Azure Document Intelligence F0 or LlamaParse Free OCR. Summaries and scores remain clearly labeled demo output.
