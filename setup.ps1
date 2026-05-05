# VerseSync local development setup (Windows PowerShell).
# Run from the repo root after extracting the skeleton zip:
#   cd E:\VerseSync
#   .\setup.ps1

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "[*] VerseSync setup starting..." -ForegroundColor Cyan
Write-Host ""

# --- Python check ---
try {
    $pyVersion = python --version 2>&1
    if ($LASTEXITCODE -ne 0) { throw }
} catch {
    Write-Host "[ERR] Python not found on PATH. Install Python 3.11+ from python.org and re-run." -ForegroundColor Red
    exit 1
}
Write-Host "[OK] $pyVersion"

# --- Move into backend ---
if (-not (Test-Path "backend")) {
    Write-Host "[ERR] backend/ folder not found. Run this script from the repo root." -ForegroundColor Red
    exit 1
}
Set-Location backend

# --- venv ---
if (-not (Test-Path "venv")) {
    Write-Host "[*] Creating virtual environment..."
    python -m venv venv
}
Write-Host "[OK] venv ready"

# --- Activate and install ---
Write-Host "[*] Installing runtime dependencies..."
& .\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
Write-Host "[OK] Runtime deps installed"

Write-Host "[*] Installing dev/test dependencies..."
pip install -r requirements-dev.txt --quiet
Write-Host "[OK] Dev deps installed"

# --- .env ---
if (-not (Test-Path ".env")) {
    Copy-Item .env.example .env
    Write-Host "[OK] .env created from template"
} else {
    Write-Host "[OK] .env already exists, skipping"
}

# --- Data dirs ---
New-Item -ItemType Directory -Force -Path "data\bibles" | Out-Null
Write-Host "[OK] Data directories ready"

# --- Smoke test ---
Write-Host ""
Write-Host "[*] Running smoke test..." -ForegroundColor Cyan
$testResult = pytest -q 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "[OK] Smoke test passed"
} else {
    Write-Host "[ERR] Smoke test failed" -ForegroundColor Red
    Write-Host $testResult
    exit 1
}

Write-Host ""
Write-Host "================================================" -ForegroundColor Green
Write-Host "[OK] Module 1 setup complete." -ForegroundColor Green
Write-Host "================================================" -ForegroundColor Green
Write-Host ""
Write-Host "To start the server:" -ForegroundColor Cyan
Write-Host "  cd backend"
Write-Host "  .\venv\Scripts\Activate.ps1"
Write-Host "  uvicorn app.main:app --reload --port 8000"
Write-Host ""
Write-Host "Then open http://localhost:8000 in a browser."
Write-Host ""
