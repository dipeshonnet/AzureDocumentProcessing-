param(
    [Parameter(Mandatory)][string]$ApiUrl,
    [string]$NodePath,
    [string]$NpmCliPath
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
if ($ApiUrl -notmatch '^https://[a-zA-Z0-9.-]+$') { throw 'ApiUrl must be an HTTPS hostname without a path.' }
if (-not $NodePath) { $NodePath = (Get-Command node -ErrorAction Stop).Source }
if (-not $NpmCliPath) {
    $npmCommand = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npmCommand) { throw 'Provide -NpmCliPath for a portable npm installation.' }
}
function Invoke-FrontendNpm([string[]]$Arguments) {
    if ($NpmCliPath) { & $NodePath $NpmCliPath @Arguments }
    else { & $npmCommand.Source @Arguments }
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
}
$oldApiUrl = $env:VITE_API_BASE_URL
$oldPath = $env:PATH
$output = Join-Path $repoRoot '.tools/cloudflare-pages'
if (-not [IO.Path]::GetFullPath($output).StartsWith([IO.Path]::GetFullPath($repoRoot) + [IO.Path]::DirectorySeparatorChar)) {
    throw 'Build output must remain within the repository.'
}
try {
    $env:PATH = (Split-Path $NodePath -Parent) + ';' + $env:PATH
    $env:VITE_API_BASE_URL = $ApiUrl
    Push-Location (Join-Path $repoRoot 'frontend')
    try {
        Invoke-FrontendNpm @('ci', '--no-audit', '--no-fund')
        Invoke-FrontendNpm @('run', 'build', '--', '--outDir', $output, '--emptyOutDir')
    } finally { Pop-Location }
    $files = Get-ChildItem -LiteralPath $output -File -Recurse
    if ($files.Count -gt 1000 -or ($files | Where-Object Length -gt 25MB)) {
        throw 'Build exceeds Cloudflare dashboard Direct Upload limits.'
    }
    $zip = Join-Path $repoRoot '.tools/cloudflare-pages.zip'
    Compress-Archive -Path (Join-Path $output '*') -DestinationPath $zip -Force
    Write-Host "Pages upload archive: $zip"
} finally {
    $env:VITE_API_BASE_URL = $oldApiUrl
    $env:PATH = $oldPath
}
