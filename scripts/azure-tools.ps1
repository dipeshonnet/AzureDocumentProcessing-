# Dot-source from deployment helpers. No authentication or provisioning on import.
$repoRoot = Split-Path $PSScriptRoot -Parent
$portableAzurePython = Join-Path $repoRoot '.tools/azure-cli/Scripts/python.exe'
$azureCommand = Get-Command az -ErrorAction SilentlyContinue
if (-not $azureCommand -and -not (Test-Path -LiteralPath $portableAzurePython)) {
    throw 'Azure CLI is missing. Run scripts/bootstrap-azure-tools.ps1.'
}
if (-not $env:AZURE_CONFIG_DIR) { $env:AZURE_CONFIG_DIR = Join-Path $repoRoot '.tools/azure-cli-config' }
$pythonExecutable = Join-Path $repoRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonExecutable)) { $pythonExecutable = (Get-Command python -ErrorAction Stop).Source }
$nodeExecutable = (Get-Command node -ErrorAction Stop).Source
$npmCommand = Get-Command npm -ErrorAction SilentlyContinue
$bundledNpm = Join-Path (Split-Path (Split-Path $nodeExecutable -Parent) -Parent) 'node_modules/npm/bin/npm-cli.js'
$portableNpm = Join-Path $repoRoot '.tools/azure-deploy/node_modules/npm/bin/npm-cli.js'
$swaCli = Join-Path $repoRoot '.tools/azure-deploy/node_modules/@azure/static-web-apps-cli/dist/cli/bin.js'
function Invoke-Azure([string[]]$Arguments) {
    if ($azureCommand) { $result = & $azureCommand.Source @Arguments }
    else { $result = & $portableAzurePython -m azure.cli @Arguments }
    if ($LASTEXITCODE -ne 0) { throw 'Azure command failed; no paid tier fallback is attempted.' }
    return $result
}
function Invoke-Npm([string[]]$Arguments) {
    if ($npmCommand) { & $npmCommand.Source @Arguments }
    elseif (Test-Path -LiteralPath $bundledNpm) { & $nodeExecutable $bundledNpm @Arguments }
    elseif (Test-Path -LiteralPath $portableNpm) { & $nodeExecutable $portableNpm @Arguments }
    else { throw 'npm is missing. Install Node.js with npm.' }
    if ($LASTEXITCODE -ne 0) { throw 'npm command failed.' }
}
function Read-DeploymentSecret([string]$EnvironmentName, [string]$Prompt) {
    $existing = [Environment]::GetEnvironmentVariable($EnvironmentName)
    if ($existing) { return $existing }
    $secure = Read-Host $Prompt -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}
