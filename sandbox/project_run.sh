#!/usr/bin/env bash
# Nexora — run a generated project inside the sandbox container.
#
# Detects the project type, installs dependencies, starts the app,
# and exposes it on a port so you can open it in a browser.
#
# Usage:
#   project_run <project_dir> [port]
#   project_run /workspace/projects/todo_api
#   project_run /workspace/projects/todo_api 9000
#
# The app will be available at http://localhost:<port> on the host
# (default port: 8080).
#
# Press Ctrl+C to stop.

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------

if [[ $# -lt 1 ]]; then
  echo "Usage: project_run <project_dir> [port]"
  exit 1
fi

PROJECT_DIR="$1"
PORT="${2:-8080}"

if [[ ! -d "$PROJECT_DIR" ]]; then
  error "Project directory does not exist: ${PROJECT_DIR}"
  exit 1
fi

cd "$PROJECT_DIR"
info "Project: ${PROJECT_DIR}"
info "Port:    ${PORT}"

# ---------------------------------------------------------------------------
# Detect language / framework
# ---------------------------------------------------------------------------

detect_and_run() {
  # ── Python (FastAPI / Flask / generic) ──────────────────────────────────
  if [[ -f "requirements.txt" ]] || ls *.py &>/dev/null 2>&1; then
    info "Detected: Python project"

    if [[ -f "requirements.txt" ]]; then
      info "Installing dependencies..."
      python3 -m pip install -q -r requirements.txt 2>/dev/null || true
    fi

    # FastAPI with uvicorn
    if grep -rql "fastapi\|FastAPI" *.py 2>/dev/null; then
      # Find the module that defines the app.
      APP_MODULE=""
      for f in main.py app.py server.py; do
        if [[ -f "$f" ]] && grep -q "FastAPI\|fastapi" "$f"; then
          APP_MODULE="${f%.py}:app"
          break
        fi
      done
      if [[ -z "$APP_MODULE" ]]; then
        APP_MODULE="main:app"
      fi
      info "Starting FastAPI: ${APP_MODULE} on port ${PORT}"
      exec python3 -m uvicorn "$APP_MODULE" --host 0.0.0.0 --port "$PORT" --reload
    fi

    # Flask
    if grep -rql "flask\|Flask" *.py 2>/dev/null; then
      FLASK_APP=""
      for f in main.py app.py server.py; do
        if [[ -f "$f" ]] && grep -q "Flask" "$f"; then
          FLASK_APP="$f"
          break
        fi
      done
      if [[ -z "$FLASK_APP" ]]; then
        FLASK_APP="app.py"
      fi
      info "Starting Flask: ${FLASK_APP} on port ${PORT}"
      exec python3 -m flask --app "$FLASK_APP" run --host 0.0.0.0 --port "$PORT"
    fi

    # Django
    if [[ -f "manage.py" ]]; then
      info "Starting Django on port ${PORT}"
      exec python3 manage.py runserver "0.0.0.0:${PORT}"
    fi

    # Generic Python — try running main.py
    if [[ -f "main.py" ]]; then
      info "Running main.py (not a web server — output below)"
      exec python3 main.py
    fi

    error "Could not determine how to run this Python project."
    error "Try adding a main.py or using FastAPI/Flask."
    exit 1
  fi

  # ── Node.js / TypeScript ────────────────────────────────────────────────
  if [[ -f "package.json" ]]; then
    info "Detected: Node.js project"
    info "Installing dependencies..."
    npm install --silent 2>/dev/null || true

    # Check for a start script.
    if node -e "const p=require('./package.json'); process.exit(p.scripts && p.scripts.start ? 0 : 1)" 2>/dev/null; then
      info "Starting via 'npm start' on port ${PORT}"
      PORT="$PORT" exec npm start
    fi

    # Try common entry points.
    for f in index.js server.js app.js main.js src/index.js; do
      if [[ -f "$f" ]]; then
        info "Starting ${f} on port ${PORT}"
        PORT="$PORT" exec node "$f"
      fi
    done

    # TypeScript
    for f in index.ts server.ts app.ts main.ts src/index.ts; do
      if [[ -f "$f" ]]; then
        info "Starting ${f} via ts-node on port ${PORT}"
        PORT="$PORT" exec npx ts-node "$f"
      fi
    done

    error "Could not determine entry point for this Node.js project."
    exit 1
  fi

  # ── Go ──────────────────────────────────────────────────────────────────
  if [[ -f "go.mod" ]] || ls *.go &>/dev/null 2>&1; then
    info "Detected: Go project"
    info "Building..."
    go build -o /tmp/nexora_app . 2>&1 || {
      error "Go build failed."
      exit 1
    }
    info "Starting Go app on port ${PORT}"
    PORT="$PORT" exec /tmp/nexora_app
  fi

  # ── Static HTML ─────────────────────────────────────────────────────────
  if [[ -f "index.html" ]]; then
    info "Detected: Static HTML site"
    info "Serving on port ${PORT} with Python http.server"
    exec python3 -m http.server "$PORT" --bind 0.0.0.0
  fi

  error "Could not detect project type. Supported: Python, Node.js, Go, static HTML."
  exit 1
}

echo ""
info "============================================"
info "  Starting project..."
info "  Open http://localhost:${PORT}"
info "  Press Ctrl+C to stop"
info "============================================"
echo ""

detect_and_run
