# Free hosting alternatives for admissions.everydayai.work

Researched October 6, 2026. This is an exploration and migration plan; no accounts, resources or DNS records were changed.

The constraint is free services only: no paid instances, expiring credits used to subsidize paid services, automatic upgrades, or billable overages. Free hosting provides limited capacity and can stop serving requests when limits are reached.

## Recommended first deployment

Use **Cloudflare Pages Free + Render Free web service + Supabase Free PostgreSQL/private storage + LlamaParse Free** for a small demonstration or pilot. This preserves the existing React and FastAPI application and removes the Azure subscription dependency. Eligibility, account billing state and actual workload capacity still need verification before deployment.

| Component | Proposed service | Free allowance and relevant limits |
| --- | --- | --- |
| React website and custom domain | Cloudflare Pages Free | 500 builds/month; static asset requests are free and unlimited. The existing subdomain can be attached to the Pages project. |
| FastAPI and in-process OCR worker | Render Free web service, Hobby workspace | 750 instance-hours/month per workspace; 5 GB included outbound bandwidth. Sleeps after 15 minutes without inbound traffic; waking takes about a minute. |
| Cases, sessions, reviews and job records | Supabase Free PostgreSQL | 500 MB database; projects pause after a week of inactivity. |
| Private original documents | Supabase Free Storage | 1 GB storage; 5 GB uncached and 5 GB cached egress. Keep the bucket private and its server key out of the frontend. |
| OCR | LlamaParse Free | 10,000 credits/month. Current cost_effective parsing is 3 credits/page, nominally about 3,333 pages before other credit usage. |

Sources: [Pages limits](https://developers.cloudflare.com/pages/platform/limits/), [static asset pricing](https://developers.cloudflare.com/pages/functions/pricing/), [Render Free](https://render.com/docs/free), [Render pricing](https://render.com/pricing), [Supabase pricing](https://supabase.com/pricing), [LlamaParse plans](https://www.llamaindex.ai/pricing), [parsing credit rates](https://developers.llamaindex.ai/llamaparse/general/pricing/).

For Render, **do not attach a payment method**: its documentation says bandwidth exhaustion then suspends free services and build exhaustion disables new builds, instead of billing supplementary usage. If an account requires a payment method or paid activation, stop and reassess. Do not use Render's Free Postgres, which expires after 30 days. Render also restricts unusually high outbound traffic, so OCR and external-storage traffic require workload testing. These hosts describe free compute as suitable for hobby/testing use, not production availability.

## Other options

| Option | Fit for this repository | Decision |
| --- | --- | --- |
| Cloudflare Workers Free + D1 + external private storage/OCR | FastAPI is supported, but Python runs in WebAssembly. Database adapters, authentication CPU use and persistent job execution need a feasibility test and migration. | Consider after the simpler deployment; more engineering work. |
| Cloudflare Tunnel to an existing computer/server | Can expose the current FastAPI service through an outbound tunnel. Hosting stays on the owner's machine. | Useful for demonstrations if a machine can remain powered and connected. Electricity, internet and maintenance remain the owner's costs. |
| Koyeb Free instance | One instance with 512 MB RAM and 0.1 vCPU; sleeps after an hour; no persistent volume. | Exclude from the initial plan: current signup documentation says the selected Pro plan is charged when entering a card. |
| Cloudflare R2 Standard | 10 GB-month storage, 1 million Class A and 10 million Class B operations/month, with free egress. | Exclude from the strict no-billing default: activation uses a subscription checkout and usage beyond allowances is billed. |

Sources: [Workers FastAPI](https://developers.cloudflare.com/workers/languages/python/packages/fastapi/), [Python package compatibility](https://developers.cloudflare.com/workers/languages/python/packages/), [Tunnel](https://developers.cloudflare.com/tunnel/), [Koyeb instance limits](https://www.koyeb.com/docs/reference/instances), [Koyeb billing/signup](https://www.koyeb.com/docs/faqs/pricing), [R2 pricing](https://developers.cloudflare.com/r2/pricing/), [R2 activation](https://developers.cloudflare.com/r2/get-started/).

Workers Free allows 100,000 dynamic requests/day and 10 ms CPU per invocation. D1 Free allows 5 million rows read/day, 100,000 written/day, 500 MB per database and 5 GB total account storage; exceeding free query limits returns errors. These are limits for a possible future architecture, not evidence this application's current backend will fit. [Workers pricing](https://developers.cloudflare.com/workers/platform/pricing/), [D1 pricing](https://developers.cloudflare.com/d1/platform/pricing/), [D1 limits](https://developers.cloudflare.com/d1/platform/limits/).

## Repository compatibility and proposed work order

The frontend is a static React/Vite build. The backend already has SQLAlchemy PostgreSQL support, PostgreSQL schema upgrades, Supabase private storage and LlamaParse. It also creates a long-lived asyncio intake worker and uses native database packages. This code inspection supports the simpler host recommendation; no live PostgreSQL or Workers compatibility test has been performed for this exploration.

1. Verify Cloudflare, Render, Supabase and LlamaParse account access and Free plans. Confirm Render has no payment method or billable add-ons before creating the service.
2. Prepare a backend deployment profile with Python dependencies appropriate for PostgreSQL, one Uvicorn process on Render's assigned port, persistent external storage and production authentication. Keep keys and passwords in backend secrets.
3. Use Supabase's session pooler for IPv4 connectivity when needed, with TLS, instead of buying its IPv4 add-on. Test schema creation, upgrades and workspace isolation on PostgreSQL. [Connection guidance](https://supabase.com/docs/guides/database/connecting-to-postgres).
4. Verify interrupted OCR recovery on process restart and prevent duplicate provider submissions where the provider job already exists. Keep the current 4 MB upload limit; account for storage uploads, OCR uploads and downloads in outbound bandwidth. Keep AI summaries/scoring labeled as simulated unless an independently verified free inference service is introduced.
5. Build React with the actual API HTTPS URL and deploy the static files to Cloudflare Pages. Translate Azure's static-site security headers into Cloudflare `_headers`, preserve secure-link referrer protection, and configure exact frontend CORS origins.
6. Attach `admissions.everydayai.work` inside the Pages project before configuring its DNS record. The existing Cloudflare zone supports this flow; preserve unrelated DNS records. [Custom-domain setup](https://developers.cloudflare.com/pages/configuration/custom-domains/).
7. Verify login, private multi-page upload/OCR, evidence history, review/sign-off, reports, exports, links and restart persistence. Render Free blocks common SMTP ports; retain manual invitation links or separately evaluate a free HTTPS email API before enabling delivery.
8. Set usage thresholds and maintain local database/file backups. Verify exhaustion stops work without upgrades. If the free compute or storage capacity is insufficient, reduce pilot scope or use an existing self-hosted server rather than provision a paid replacement.

Deployment remains a separate implementation step. Keep Azure scripts available as historical preparation; do not execute them for this alternative profile.
