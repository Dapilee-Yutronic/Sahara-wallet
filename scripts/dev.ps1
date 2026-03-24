# Run Sahara locally with hot reload. No Railway needed while you iterate.
# Usage: from repo root, run:  .\scripts\dev.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "Python is not on PATH. Install Python 3.11+ and try again."
    exit 1
}

if (-not (Test-Path ".venv")) {
    Write-Host "Creating .venv ..."
    python -m venv .venv
}

$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    python -m venv .venv
}

& (Join-Path $root ".venv\Scripts\Activate.ps1")
pip install -r requirements.txt -q

# Drop inherited DATABASE_URL so a stray shell var does not override `.env` / local SQLite.
Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue

Write-Host "Sahara at http://127.0.0.1:8000  (Ctrl+C to stop)"
Write-Host ""

python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
