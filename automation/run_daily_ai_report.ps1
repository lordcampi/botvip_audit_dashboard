$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}
& $Python ".\automation\run_daily_ai_report.py" --window daily --output reports --max-ai-chars 95000
exit $LASTEXITCODE

