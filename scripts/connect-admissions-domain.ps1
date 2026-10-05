param(
    [string]$Domain = 'admissions.everydayai.work',
    [string]$OutputsFile = ''
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'azure-tools.ps1')
if (-not $OutputsFile) { $OutputsFile = Join-Path $repoRoot '.tools/azure-free-outputs.json' }
$outputs = Get-Content -LiteralPath $OutputsFile -Raw | ConvertFrom-Json
if (('https://' + $Domain) -ne $outputs.publicAppUrl) { throw 'Domain must match the deployed PUBLIC_APP_URL and CORS origin.' }
Write-Host ('Required Cloudflare record: DNS-only CNAME ' + $Domain + ' -> ' + $outputs.cnameTarget)
Invoke-Azure @('staticwebapp','hostname','set','--name',$outputs.frontendName,'--resource-group',$outputs.resourceGroup,
    '--hostname',$Domain,'--validation-method','cname-delegation','--output','none')
Invoke-Azure @('staticwebapp','hostname','list','--name',$outputs.frontendName,'--resource-group',$outputs.resourceGroup,'--output','table')
