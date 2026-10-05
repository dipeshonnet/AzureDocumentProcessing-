param(
    [Parameter(Mandatory)][string]$SubscriptionId,
    [Parameter(Mandatory)][string]$ContainerImage,
    [Parameter(Mandatory)][string]$AdminEmail,
    [string]$ResourceGroup = 'admissions-mvp-rg',
    [string]$Location = 'centralindia',
    [switch]$Deploy
)
$ErrorActionPreference = 'Stop'
throw 'This profile can incur charges and is disabled for the approved free-only MVP. Use scripts/deploy-azure-free.ps1.'
if (-not (Get-Command az -ErrorAction SilentlyContinue)) { throw 'Install Azure CLI and run az login first.' }
$repoRoot = Split-Path $PSScriptRoot -Parent
function Invoke-Azure {
    param([string[]]$Arguments)
    & az @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'Azure CLI command failed. See the preceding Azure error.' }
}
Invoke-Azure -Arguments @('account', 'set', '--subscription', $SubscriptionId)
Invoke-Azure -Arguments @('group', 'create', '--name', $ResourceGroup, '--location', $Location, '--output', 'none')
$sqlSecret = Read-Host 'New SQL admin password (16+ characters, including upper/lowercase, digits and symbol)' -AsSecureString
$adminSecret = Read-Host 'New application administrator password (16+ characters)' -AsSecureString
function Get-PlainText([Security.SecureString]$Secret) {
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secret)
    try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}
$sqlPassword = Get-PlainText $sqlSecret
$adminPassword = Get-PlainText $adminSecret
if ($sqlPassword.Length -lt 16 -or $adminPassword.Length -lt 16) { throw 'Passwords must have at least 16 characters.' }
# Keep passwords out of command arguments, shell history and source control.
$parameterFile = Join-Path ([IO.Path]::GetTempPath()) ('admissions-' + [guid]::NewGuid() + '.json')
try {
    $parameters = @{
        '$schema' = 'https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#'
        contentVersion = '1.0.0.0'
        parameters = @{
            containerImage = @{ value = $ContainerImage }
            bootstrapAdminEmail = @{ value = $AdminEmail }
            sqlAdminPassword = @{ value = $sqlPassword }
            bootstrapAdminPassword = @{ value = $adminPassword }
            location = @{ value = $Location }
        }
    }
    $parameters | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $parameterFile -Encoding utf8
    $baseArguments = @('--resource-group', $ResourceGroup, '--template-file', (Join-Path $repoRoot 'infra/azure-mvp.bicep'), '--parameters', ('@' + $parameterFile))
    Invoke-Azure -Arguments (@('deployment', 'group', 'validate') + $baseArguments + @('--output', 'none'))
    Invoke-Azure -Arguments (@('deployment', 'group', 'what-if') + $baseArguments)
    if ($Deploy) {
        Invoke-Azure -Arguments (@('deployment', 'group', 'create', '--name', 'admissions-mvp') + $baseArguments + @('--query', 'properties.outputs', '--output', 'json'))
    } else {
        Write-Host 'Preview complete. Run again with -Deploy to provision the MVP.'
    }
} finally {
    if (Test-Path -LiteralPath $parameterFile) { Remove-Item -LiteralPath $parameterFile -Force }
    $sqlPassword = $null
    $adminPassword = $null
}
