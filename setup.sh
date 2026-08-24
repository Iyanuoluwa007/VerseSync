#!/usr/bin/env bash
# VerseSync setup (macOS / Linux).
#
#   ./setup.sh                projector-only profile (fast, no GPU needed)
#   ./setup.sh --with-stt     also install the speech-to-text stack (large)
#   ./setup.sh --skip-bibles  skip downloading and ingesting the Bibles
#
# Run it from the repository root. It is safe to re-run.

set -euo pipefail

WITH_STT=0
SKIP_BIBLES=0
for arg in "$@"; do
    case "$arg" in
        --with-stt) WITH_STT=1 ;;
        --skip-bibles) SKIP_BIBLES=1 ;;
        -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
VENV_DIR="$BACKEND_DIR/.venv"
VENV_PY="$VENV_DIR/bin/python"

step() { printf '\033[36m[*]\033[0m %s\n' "$1"; }
ok()   { printf '\033[32m[OK]\033[0m %s\n' "$1"; }
fail() { printf '\033[31m[ERR]\033[0m %s\n' "$1" >&2; }

echo
step "VerseSync setup"
echo

# --- Python ---------------------------------------------------------
PYTHON_BIN="${PYTHON:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    fail "python3 not found. Install Python 3.11 or newer and re-run."
    exit 1
fi

if ! "$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    fail "$("$PYTHON_BIN" --version) found, but VerseSync needs Python 3.11 or newer."
    exit 1
fi
ok "$("$PYTHON_BIN" --version)"

[ -d "$BACKEND_DIR" ] || { fail "backend/ not found next to this script."; exit 1; }

# --- Virtual environment --------------------------------------------
if [ ! -x "$VENV_PY" ]; then
    step "Creating virtual environment in backend/.venv"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
ok "Virtual environment ready"

# --- Dependencies ---------------------------------------------------
step "Installing dependencies"
"$VENV_PY" -m pip install --upgrade pip --quiet
"$VENV_PY" -m pip install -r "$BACKEND_DIR/requirements-dev.txt" --quiet
ok "Runtime and dev dependencies installed"

if [ "$WITH_STT" -eq 1 ]; then
    step "Installing speech-to-text stack (this pulls in torch; it is large)"
    if "$VENV_PY" -m pip install -r "$BACKEND_DIR/requirements-stt.txt"; then
        ok "STT dependencies installed"
    else
        fail "STT dependencies failed. The projector and API still work; see the README troubleshooting section."
    fi
fi

# --- .env -----------------------------------------------------------
if [ ! -f "$BACKEND_DIR/.env" ]; then
    cp "$BACKEND_DIR/.env.example" "$BACKEND_DIR/.env"
    ok ".env created from the template"
else
    ok ".env already exists, leaving it alone"
fi

mkdir -p "$BACKEND_DIR/data/bibles"

# --- Bible data -----------------------------------------------------
if [ "$SKIP_BIBLES" -eq 0 ]; then
    step "Downloading Bible translations from eBible.org (about 7 MB)"
    if "$VENV_PY" "$REPO_ROOT/scripts/download_bibles.py"; then
        step "Ingesting into SQLite (about 8 seconds)"
        if "$VENV_PY" "$REPO_ROOT/scripts/ingest_bibles.py"; then
            ok "93,287 verses across KJV, WEB and YOR are ready"
        else
            fail "Ingest failed. Re-run: python scripts/ingest_bibles.py"
        fi
    else
        fail "Bible download failed. Check your connection, then run: python scripts/download_bibles.py"
    fi
fi

# --- Tests ----------------------------------------------------------
echo
step "Running the test suite"
(cd "$BACKEND_DIR" && "$VENV_PY" -m pytest -q) || {
    fail "Tests failed. Please open an issue with the output above."
    exit 1
}
ok "Tests passed"

# --- Done -----------------------------------------------------------
cat <<'BANNER'

================================================
 VerseSync is ready.
================================================

Start the server:
  cd backend
  source .venv/bin/activate
  uvicorn app.main:app --port 8000

Then:
  http://localhost:8000/projector   <- add this as an OBS Browser Source
  http://localhost:8000/docs        <- interactive API docs

BANNER

if [ "$WITH_STT" -eq 0 ]; then
    echo "Live microphone transcription was not installed."
    echo "Add it later with:  ./setup.sh --with-stt"
    echo
fi
