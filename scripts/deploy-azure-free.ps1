param(
    [Parameter(Mandatory)][string]$SubscriptionId,
    [Parameter(Mandatory)][string]$SupabaseUrl,
    [Parameter(Mandatory)][string]$AdminEmail,
    [ValidateSet('azure','llamaparse','mock')][string]$OcrProvider = 'llamaparse',
    [string]$ResourceGroup = 'admissions-free-rg',
    [string]$Location = 'centralindia',
    [string]$FrontendLocation = 'eastasia',
    [string]$PublicAppUrl = 'https://admissions.everydayai.work',
    [ValidateSet('ODBC Driver 17 for SQL Server','ODBC Driver 18 for SQL Server')]
    [string]$SqlOdbcDriver = 'ODBC Driver 18 for SQL Server',
    [switch]$Deploy
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot 'azure-tools.ps1')
if ($PublicAppUrl -notmatch '^https://[a-zA-Z0-9.-]+$') { throw 'PublicAppUrl must be an HTTPS hostname without a trailing slash or path.' }
if ($SupabaseUrl -notmatch '^https://[a-zA-Z0-9.-]+/?$') { throw 'SupabaseUrl must be an HTTPS project URL.' }
if ($Deploy -and -not (Test-Path -LiteralPath $swaCli)) { throw 'Run scripts/bootstrap-azure-tools.ps1 before deployment.' }
Invoke-Azure @('account','set','--subscription',$SubscriptionId)
$groupExists = (Invoke-Azure @('group','exists','--name',$ResourceGroup,'--output','tsv')) -join ''
if ($groupExists -ne 'true') {
    if (-not $Deploy) { throw 'Preview needs an existing resource group. Create the empty group first, or run with -Deploy for validation, what-if and deployment.' }
    Invoke-Azure @('group','create','--name',$ResourceGroup,'--location',$Location,'--output','none')
}
$sqlPassword = Read-DeploymentSecret 'SQL_ADMIN_PASSWORD' 'SQL administrator password (16+ characters, mixed case, digit and symbol)'
$adminPassword = Read-DeploymentSecret 'BOOTSTRAP_ADMIN_PASSWORD' 'Application administrator password (16+ characters)'
$supabaseKey = Read-DeploymentSecret 'SUPABASE_SERVICE_KEY' 'Supabase server-only secret or legacy service_role key'
$llamaKey = if ($OcrProvider -eq 'llamaparse') { Read-DeploymentSecret 'LLAMA_CLOUD_API_KEY' 'LlamaParse API key from a Free plan account' } else { '' }
if ($sqlPassword.Length -lt 16 -or $adminPassword.Length -lt 16 -or -not $supabaseKey -or ($OcrProvider -eq 'llamaparse' -and -not $llamaKey)) {
    throw 'Supply strong passwords and all required provider keys.'
}
$parameterFile = Join-Path ([IO.Path]::GetTempPath()) ('admissions-free-' + [guid]::NewGuid() + '.json')
$previousApi = $env:VITE_API_BASE_URL
$previousToken = $env:SWA_CLI_DEPLOYMENT_TOKEN
try {
    $values = @{ location=$Location; frontendLocation=$FrontendLocation; publicAppUrl=$PublicAppUrl;
        bootstrapAdminEmail=$AdminEmail; supabaseUrl=$SupabaseUrl.TrimEnd('/');
        supabaseServiceKey=$supabaseKey; sqlAdminPassword=$sqlPassword; bootstrapAdminPassword=$adminPassword;
        llamaCloudApiKey=$llamaKey; ocrProvider=$OcrProvider; sqlOdbcDriver=$SqlOdbcDriver }
    $parameters = @{}
    foreach ($key in $values.Keys) { $parameters[$key] = @{ value=$values[$key] } }
    @{ '$schema'='https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#'; contentVersion='1.0.0.0'; parameters=$parameters } |
        ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $parameterFile -Encoding utf8
    $base = @('--resource-group',$ResourceGroup,'--template-file',(Join-Path $repoRoot 'infra/azure-free-mvp.bicep'),'--parameters',('@'+$parameterFile))
    Invoke-Azure (@('deployment','group','validate') + $base + @('--output','none'))
    # App settings can contain secrets. ResourceIdOnly omits all property values.
    Invoke-Azure (@('deployment','group','what-if') + $base + @('--result-format','ResourceIdOnly'))
    if (-not $Deploy) { Write-Host 'Free-tier preview complete. Use -Deploy to provision and publish.'; return }
    $result = Invoke-Azure (@('deployment','group','create','--name','admissions-free') + $base + @('--query','properties.outputs','--output','json'))
    $outputs = ($result -join "`n") | ConvertFrom-Json
    $env:VITE_API_BASE_URL = $outputs.appUrl.value
    Push-Location (Join-Path $repoRoot 'frontend')
    try {
        Invoke-Npm @('ci','--no-audit','--no-fund')
        Invoke-Npm @('run','build')
    } finally { Pop-Location }
    & $pythonExecutable (Join-Path $PSScriptRoot 'package-azure-free.py')
    if ($LASTEXITCODE -ne 0) { throw 'Backend packaging failed.' }
    Invoke-Azure @('webapp','deploy','--resource-group',$ResourceGroup,'--name',$outputs.appName.value,
        '--src-path',(Join-Path $repoRoot '.tools/azure-free-mvp.zip'),'--type','zip','--output','none')
    $env:SWA_CLI_DEPLOYMENT_TOKEN = (Invoke-Azure @('staticwebapp','secrets','list','--name',$outputs.frontendName.value,
        '--resource-group',$ResourceGroup,'--query','properties.apiKey','--output','tsv')) -join ''
    if (-not $env:SWA_CLI_DEPLOYMENT_TOKEN) { throw 'Azure did not return a frontend deployment token.' }
    & $nodeExecutable $swaCli deploy (Join-Path $repoRoot 'frontend/dist') --env production
    if ($LASTEXITCODE -ne 0) { throw 'Frontend deployment failed; the backend remains deployed for inspection.' }
    $publicOutputs = [ordered]@{ resourceGroup=$ResourceGroup; appName=$outputs.appName.value; apiUrl=$outputs.appUrl.value;
        frontendName=$outputs.frontendName.value; frontendUrl=$outputs.frontendUrl.value;
        cnameTarget=$outputs.frontendHostname.value; publicAppUrl=$PublicAppUrl; ocrProvider=$OcrProvider }
    $publicOutputs | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $repoRoot '.tools/azure-free-outputs.json') -Encoding utf8
    Write-Host ('Frontend: ' + $outputs.frontendUrl.value)
    Write-Host ('API: ' + $outputs.appUrl.value)
    Write-Host ('Cloudflare DNS-only CNAME admissions -> ' + $outputs.frontendHostname.value)
    Write-Host 'Verify the default frontend and API, then add the custom domain using scripts/connect-admissions-domain.ps1.'
} finally {
    if (Test-Path -LiteralPath $parameterFile) { Remove-Item -LiteralPath $parameterFile -Force }
    $env:VITE_API_BASE_URL = $previousApi
    $env:SWA_CLI_DEPLOYMENT_TOKEN = $previousToken
    $sqlPassword = $adminPassword = $supabaseKey = $llamaKey = $null
}
