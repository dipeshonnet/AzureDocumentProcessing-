# Free Azure deployment at admissions.everydayai.work

The selected profile is **Static Web Apps Free + App Service Linux F1 + Azure SQL free offer + Supabase Free private storage + LlamaParse Free OCR**. The domain remains in Cloudflare. Azure resources and DNS have not been deployed by these preparation steps.

Deployment is restricted to free service tiers and allowances. Do not provision paid replacements, use trial credits to cover paid services, enable provider overages, or upgrade account billing automatically. If a required free service is unavailable or exhausted, stop that operation. An active eligible Azure subscription is still required to provision free resources.

The frontend is public static code; authentication and all admissions permissions remain in FastAPI. The browser calls the API's default HTTPS azurewebsites.net hostname directly. Free Static Web Apps does not support linking an App Service backend: that integration requires Standard. F1 cannot bind a custom hostname itself.

## Limits and behavior

| Service | Allowance |
| --- | --- |
| Static Web Apps Free | 100 GB bandwidth/month, 250 MB/environment, two custom domains, managed HTTPS |
| App Service Linux F1 | 60 CPU minutes/day, 3 CPU minutes per five-minute window, 1 GB RAM, 1 GB storage, 165 MB outgoing bandwidth/day |
| Azure SQL free offer | 100,000 vCore-seconds/month, 32 GB data and 32 GB backup; AutoPause on allowance exhaustion |
| Supabase Free | 1 GB private file storage, 5 GB uncached egress/month; inactivity may pause the project |
| LlamaParse Free | 10,000 credits/month; cost_effective parsing costs 3 credits/page (about 3,333 pages if credits are used only for parsing) |

The app enforces **4 MB per upload**, independently of LlamaParse's higher file limits. Longer PDFs are supported with LlamaParse; Azure F0's two-page restriction does not apply to it. Actual available credits depend on other provider usage. Keep the LlamaParse account on Free; the API key does not prove account billing status. There is no paid provider fallback or automatic retry.

Sources checked October 4, 2026: [SWA quotas](https://learn.microsoft.com/en-us/azure/static-web-apps/quotas), [App Service limits](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/azure-subscription-service-limits), [daily quota enforcement](https://learn.microsoft.com/en-us/azure/app-service/web-sites-monitor), [SQL free offer](https://learn.microsoft.com/en-us/azure/azure-sql/database/free-offer), [Supabase storage](https://supabase.com/docs/guides/storage/pricing), [egress](https://supabase.com/docs/guides/platform/manage-your-usage/egress), [project pausing](https://supabase.com/docs/guides/platform/free-project-pausing), [LlamaParse plans](https://www.llamaindex.ai/pricing), [credit rates](https://developers.llamaindex.ai/llamaparse/general/pricing/).

Summaries, structured extraction and automatic rubric scoring remain simulated and labeled as demo output. Real OCR and manual admissions review are separate. No Azure OpenAI deployment is created.

## 1. Install tools and sign in

Run from the repository root in PowerShell:

```powershell
.\scripts\bootstrap-azure-tools.ps1
.\scripts\login-azure.ps1
```

The helper uses device-code sign-in: complete Microsoft authentication yourself in a browser. Azure CLI, SWA CLI and authentication state are under ignored `.tools` when using the bundled tools. Treat that folder as private. Do not share it or upload it.

Microsoft sign-in alone does not create an Azure subscription. If the CLI reports `No subscriptions found`, check **Subscriptions** and **Directories + subscriptions** in Azure Portal. Activate an eligible subscription or sign in with the account that already owns one. For a directory-specific MFA requirement, run `scripts/login-azure.ps1 -TenantId "YOUR-DIRECTORY-ID"` and complete the new browser sign-in. Subscription activation, identity/card verification and acceptance of billing terms must be completed by the account owner.

The [Azure Free account](https://azure.microsoft.com/en-us/free/) trial is available only to eligible new customers. Its spending protection does not mean indefinite account access: after 30 days or exhaustion of the trial credit, Microsoft requires a move to pay-as-you-go to continue. The resource SKUs in this template have free allowances, but an active eligible subscription is still required. Do not select or accept a paid account upgrade automatically.

Select an eligible Azure subscription. Confirm Linux F1, the SQL free offer and the frontend region are available. The backend defaults to centralindia; Static Web Apps defaults independently to eastasia. If a free SKU is unavailable, stop instead of selecting a paid replacement.

## 2. Prepare external Free accounts

Sign in to Supabase and LlamaParse in the browser.

- Supabase: select a Free project, create private bucket `admissions-raw`, set maximum upload size to 4 MB, and obtain the project URL and server-only key. The bucket must not be public.
- LlamaParse: confirm Free plan with no pay-as-you-go upgrade and create a project API key. Only OCR parsing uses it. Files are uploaded with purpose `parse`; the connector attempts to delete its upload after success or failure. Failed cleanup is logged without provider credentials. Inspect provider files if cleanup fails; its documented Parse source retention is 48 hours.
- Use private deployment prompts for keys and SQL/application passwords. Do not paste secrets into chat or frontend environment variables. Process environment variables with the documented names may also supply secrets for an authorized automated run.

FastAPI authorizes original-file downloads before using the Supabase server key. Originals and replacement history count toward storage; removing SQL metadata alone does not remove bucket objects.

## 3. Validate and deploy

For a preview, first create the empty resource group through Azure Portal. Preview mode performs validation and ResourceIdOnly what-if; it does not create the group or deploy resources.

```powershell
.\scripts\deploy-azure-free.ps1 `
  -SubscriptionId "YOUR-SUBSCRIPTION-ID" `
  -SupabaseUrl "https://YOUR-PROJECT.supabase.co" `
  -AdminEmail "YOUR-ADMIN-EMAIL"
```

Repeat with `-Deploy` to provision and publish. Deployment mode can create its resource group. The script defaults to `-OcrProvider llamaparse`.

Secrets: `SQL_ADMIN_PASSWORD`, `BOOTSTRAP_ADMIN_PASSWORD`, `SUPABASE_SERVICE_KEY`, `LLAMA_CLOUD_API_KEY`. SQL and administrator passwords need at least 16 characters. The script prompts privately for missing secrets and deletes its temporary ARM parameter file afterward. What-if omits resource property values to avoid displaying app settings.

The script builds React with the actual API hostname, packages backend source separately, publishes both services, and writes public resource metadata to `.tools/azure-free-outputs.json`. No custom domain is activated before default-host verification.

Backend settings include:
- `PUBLIC_APP_URL=https://admissions.everydayai.work`
- CORS restricted to that domain and the generated frontend hostname
- `DEV_AUTH_ENABLED=false`, `EXPOSE_LEGACY_API=false`
- `INTAKE_QUEUE_WATCHDOG_SECONDS=0`
- SQL `useFreeLimit=true`, `freeLimitExhaustionBehavior=AutoPause`
- one Uvicorn process and no Always On

SQL connections use TLS and certificate verification. Confirm the installed ODBC driver through App Service SSH if startup fails: `python -c "import pyodbc; print(pyodbc.drivers())"`. Redeploy with `-SqlOdbcDriver "ODBC Driver 17 for SQL Server"` if only Driver 17 exists; keep certificate verification enabled.

Keep the original SQL administrator password on redeploy. Bootstrap settings do not overwrite an existing application's user password. Startup initializes/upgrades the schema using the current additive, idempotent workflow migration. Back up an existing database before updating it.

## 4. Verify default addresses

Check API `/health`, sign in on the generated frontend URL, create a fictional university and configure external parser approval. Verify a readable three-page PDF under 4 MB returns all pages.

The optional authenticated smoke test uses a fictional three-page fixture and consumes OCR credits:

```powershell
.venv\Scripts\python.exe scripts/smoke-azure-mvp.py `
  --url "https://BACKEND.azurewebsites.net" `
  --frontend-url "https://FRONTEND.azurestaticapps.net" `
  --email "YOUR-ADMIN-EMAIL" --live-ocr
```

It checks HTTPS, CORS, login, SQL, private storage, processing, record download and logout. Synthetic smoke records remain for inspection. For mock deployments omit `--live-ocr`.

In the browser also verify membership isolation, secure links, invitation acceptance, source viewing, autosave/sign-off, reports and exports. Restart the backend and verify sessions, files and interrupted jobs persist.

## 5. Connect Cloudflare

Use the real `cnameTarget` from deployment outputs:

| Type | Name | Target | Proxy | TTL |
| --- | --- | --- | --- | --- |
| CNAME | admissions | generated hostname ending in azurestaticapps.net | DNS only | Auto |

Inspect any existing admissions record before changing it. Other everydayai.work records remain unchanged. After DNS propagation run:

```powershell
.\scripts\connect-admissions-domain.ps1
```

Azure validates the CNAME and manages HTTPS. Verify the domain certificate and repeat login, secure uploads and invitation acceptance at the custom domain. Invitation email needs a separately configured SMTP service; manual link sharing remains available.

References: [external custom domain setup](https://learn.microsoft.com/en-us/azure/static-web-apps/custom-domain-external), [Cloudflare DNS-only](https://developers.cloudflare.com/dns/proxy-status/), [paid backend linking](https://learn.microsoft.com/en-us/azure/static-web-apps/apis-app-service).

## 6. Operate within free limits

Idle workers wait for the in-memory queue without querying SQL. Persisted interrupted jobs are recovered on startup. An optional positive watchdog interval remains available for installations that need polling; leave it zero for this single-process free deployment. Browser polling runs only while documents are pending.

F1 can unload while idle and stop when CPU or bandwidth limits are reached. SQL can pause until next month's renewal. This is a small demo/pilot profile with no availability guarantee. Repeated original-file downloads consume F1 outbound bandwidth as well as Supabase egress.

Check service dashboards before larger imports. Add the documented free SQL `Free amount remaining` alert at 10,000 vCore-seconds. Track F1 quotas and LlamaParse credits; budget alerts do not impose spending caps. Do not add paid storage, registries, monitoring, model deployments or automatic upgrades to this profile.

Maintain a local SQL export and an independently recoverable copy of private files before migrations or teardown. Test restore with fictional data. Export needed records before deleting Azure resources. Supabase and LlamaParse are external accounts and require separate cleanup.
