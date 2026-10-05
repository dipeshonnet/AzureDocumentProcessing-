param([string]$Python = '')
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
if (-not $Python) {
    $Python = Join-Path $repoRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $Python)) { $Python = (Get-Command python -ErrorAction Stop).Source }
}
$portableAzure = Join-Path $repoRoot '.tools/azure-cli'
if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    if (-not (Test-Path -LiteralPath (Join-Path $portableAzure 'Scripts/python.exe'))) {
        & $Python -m venv $portableAzure
        if ($LASTEXITCODE -ne 0) { throw 'Could not create isolated Azure CLI environment.' }
    }
    & (Join-Path $portableAzure 'Scripts/python.exe') -m pip install 'azure-cli>=2.80,<3' --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { throw 'Azure CLI installation failed.' }
}
. (Join-Path $PSScriptRoot 'azure-tools.ps1')
$bundledPnpm = Join-Path (Split-Path (Split-Path $nodeExecutable -Parent) -Parent) 'node_modules/pnpm/bin/pnpm.cjs'
if (-not $npmCommand -and -not (Test-Path -LiteralPath $bundledNpm) -and (Test-Path -LiteralPath $bundledPnpm)) {
    New-Item -ItemType Directory -Path (Join-Path $repoRoot '.tools/azure-deploy') -Force | Out-Null
    & $nodeExecutable $bundledPnpm add --ignore-scripts --dir (Join-Path $repoRoot '.tools/azure-deploy') 'npm@10' '@azure/static-web-apps-cli@2'
    if ($LASTEXITCODE -ne 0) { throw 'Deployment tool installation failed.' }
} else {
    Invoke-Npm @('install','--prefix',(Join-Path $repoRoot '.tools/azure-deploy'),'--no-audit','--no-fund','@azure/static-web-apps-cli@2')
}
if (-not (Test-Path -LiteralPath $swaCli)) { throw 'SWA CLI installation did not produce its executable.' }
Write-Host 'Deployment tools installed under ignored .tools. Run the login helper next.'
