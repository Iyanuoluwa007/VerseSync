# VerseSync setup (Windows PowerShell).
#
#   .\setup.ps1              projector-only profile (fast, no GPU needed)
#   .\setup.ps1 -WithSTT     also install the speech-to-text stack (large)
#   .\setup.ps1 -SkipBibles  skip downloading and ingesting the Bibles
#
# Run it from the repository root. It is safe to re-run.

[CmdletBinding()]
param(
    [switch]$WithSTT,
    [switch]$SkipBibles
)

# "Stop" applies to CMDLETS. It must NOT apply to native executables:
# in Windows PowerShell 5.1 any stderr output from a native command
# (pip printing a retry warning, for example) is turned into a
# terminating error even when the command exits 0. Native commands are
# invoked through Invoke-Native below, which checks the exit code -- the
# only thing that actually indicates failure.
$ErrorActionPreference = "Stop"

function Invoke-Native {
    param([Parameter(Mandatory)][string]$Exe,
          [Parameter(ValueFromRemainingArguments)][string[]]$Arguments)

    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        # Out-Host, not a bare call: a bare call would put the command's
        # stdout on the success stream, so this function would return
        # [output..., exitcode] instead of just the exit code.
        & $Exe @Arguments 2>&1 | Out-Host
    } finally {
        $ErrorActionPreference = $previous
    }
    return $LASTEXITCODE
}

# Anchor every path to this script's own location, so the repo works
# wherever it is cloned.
$RepoRoot = $PSScriptRoot
$BackendDir = Join-Path $RepoRoot "backend"
$VenvDir = Join-Path $BackendDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

function Write-Step($msg) { Write-Host "[*] $msg" -ForegroundColor Cyan }
function Write-Ok($msg) { Write-Host "[OK] $msg" -ForegroundColor Green }
function Write-Fail($msg) { Write-Host "[ERR] $msg" -ForegroundColor Red }

Write-Host ""
Write-Step "VerseSync setup"
Write-Host ""

# --- Python ---------------------------------------------------------
$pyVersion = $null
try {
    $pyVersion = (& python --version 2>&1 | Out-String).Trim()
} catch {
    $pyVersion = $null
}
if (-not $pyVersion) {
    Write-Fail "Python not found on PATH. Install Python 3.11 or newer from python.org and re-run."
    exit 1
}

# faster-whisper and its wheels lag new Python releases, so check the
# version rather than letting pip fail confusingly later.
$versionMatch = [regex]::Match($pyVersion, '(\d+)\.(\d+)')
if ($versionMatch.Success) {
    $major = [int]$versionMatch.Groups[1].Value
    $minor = [int]$versionMatch.Groups[2].Value
    if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 11)) {
        Write-Fail "$pyVersion found, but VerseSync needs Python 3.11 or newer."
        exit 1
    }
}
Write-Ok $pyVersion

if (-not (Test-Path $BackendDir)) {
    Write-Fail "backend/ not found next to this script. Run setup.ps1 from the repository root."
    exit 1
}

# --- Virtual environment --------------------------------------------
if (-not (Test-Path $VenvPython)) {
    Write-Step "Creating virtual environment in backend\.venv"
    if ((Invoke-Native python -m venv $VenvDir) -ne 0) {
        Write-Fail "Could not create the virtual environment."
        exit 1
    }
}
Write-Ok "Virtual environment ready"

# --- Dependencies ---------------------------------------------------
Write-Step "Installing dependencies"
$null = Invoke-Native $VenvPython -m pip install --upgrade pip --quiet
$depCode = Invoke-Native $VenvPython -m pip install -r (Join-Path $BackendDir "requirements-dev.txt") --quiet
if ($depCode -ne 0) { Write-Fail "Dependency installation failed."; exit 1 }
Write-Ok "Runtime and dev dependencies installed"

if ($WithSTT) {
    Write-Step "Installing speech-to-text stack (this pulls in torch; it is large)"
    $sttCode = Invoke-Native $VenvPython -m pip install -r (Join-Path $BackendDir "requirements-stt.txt")
    if ($sttCode -ne 0) {
        Write-Fail "STT dependencies failed to install. The projector and API still work; see the README troubleshooting section."
    } else {
        Write-Ok "STT dependencies installed"
    }
}

# --- .env -----------------------------------------------------------
$EnvFile = Join-Path $BackendDir ".env"
if (-not (Test-Path $EnvFile)) {
    Copy-Item (Join-Path $BackendDir ".env.example") $EnvFile
    Write-Ok ".env created from the template"
} else {
    Write-Ok ".env already exists, leaving it alone"
}

New-Item -ItemType Directory -Force -Path (Join-Path $BackendDir "data\bibles") | Out-Null

# --- Bible data -----------------------------------------------------
if (-not $SkipBibles) {
    Write-Step "Downloading Bible translations from eBible.org (about 7 MB)"
    $dlCode = Invoke-Native $VenvPython (Join-Path $RepoRoot "scripts\download_bibles.py")
    if ($dlCode -ne 0) {
        Write-Fail "Bible download failed. Check your connection, then run: python scripts\download_bibles.py"
    } else {
        Write-Step "Ingesting into SQLite (about 8 seconds)"
        $ingestCode = Invoke-Native $VenvPython (Join-Path $RepoRoot "scripts\ingest_bibles.py")
        if ($ingestCode -ne 0) {
            Write-Fail "Ingest failed. Re-run: python scripts\ingest_bibles.py"
        } else {
            Write-Ok "93,287 verses across KJV, WEB and YOR are ready"
        }
    }
}

# --- Tests ----------------------------------------------------------
Write-Host ""
Write-Step "Running the test suite"
Push-Location $BackendDir
try {
    $testsPassed = ((Invoke-Native $VenvPython -m pytest -q) -eq 0)
} finally {
    Pop-Location
}

if (-not $testsPassed) {
    Write-Fail "Tests failed. Please open an issue with the output above."
    exit 1
}
Write-Ok "Tests passed"

# --- Done -----------------------------------------------------------
Write-Host ""
Write-Host "================================================" -ForegroundColor Green
Write-Host " VerseSync is ready." -ForegroundColor Green
Write-Host "================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Start the server:" -ForegroundColor Cyan
Write-Host "  cd backend"
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host "  uvicorn app.main:app --port 8000"
Write-Host ""
Write-Host "Then:" -ForegroundColor Cyan
Write-Host "  http://localhost:8000/projector   <- add this as an OBS Browser Source"
Write-Host "  http://localhost:8000/docs        <- interactive API docs"
Write-Host ""
if (-not $WithSTT) {
    Write-Host "Live microphone transcription was not installed." -ForegroundColor Yellow
    Write-Host "Add it later with:  .\setup.ps1 -WithSTT"
    Write-Host ""
}
