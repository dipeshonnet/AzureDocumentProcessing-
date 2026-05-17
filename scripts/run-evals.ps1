param(
    [ValidateSet("mock", "azure")]
    [string]$Provider = "mock",

    [switch]$EnableAzureOpenAI,

    [string]$Output
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

Push-Location $RepoRoot
try {
    $EvalArgs = @("-m", "evals.run_evals", "--provider", $Provider)
    if ($EnableAzureOpenAI) {
        $EvalArgs += "--enable-azure-openai"
    }
    if ($Output) {
        $EvalArgs += @("--output", $Output)
    }

    & python @EvalArgs
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
