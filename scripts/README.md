# Scripts

Developer and CI helpers.
Use scripts for repeatable maintenance tasks.

Keep commands documented.
Avoid local secrets.

For the selected Cloudflare/Render deployment, use `build-cloudflare-pages.ps1`
after obtaining the real Render API URL. Follow [the Free deployment guide](../docs/cloudflare-render-free.md).

For the previously considered free Azure deployment, run `bootstrap-azure-tools.ps1`, then
`login-azure.ps1` for browser sign-in. `deploy-azure-free.ps1` validates and
previews by default; `-Deploy` publishes the backend and static frontend with
LlamaParse OCR. After setting Cloudflare DNS, `connect-admissions-domain.ps1`
validates the custom domain. Follow [the full deployment guide](../docs/azure-free-tier-mvp.md).
