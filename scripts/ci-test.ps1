$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

Push-Location $RepoRoot
try {
    python -m pip install -r requirements.txt
    python -B -m pytest backend/tests evals -p no:cacheprovider
    python -m evals.run_evals

    Push-Location frontend
    try {
        npm.cmd ci
        npm.cmd test
        npm.cmd run build
    }
    finally {
        Pop-Location
    }
}
finally {
    Pop-Location
}
