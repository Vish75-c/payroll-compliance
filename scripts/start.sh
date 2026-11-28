#!/usr/bin/env bash
# One command to run everything locally (macOS / Linux):
#   ./scripts/start.sh
# Starts: Temporal (state on disk), the workflow worker, the Demo HRMS, and the web app on :8080.
# Everything keeps its state in ./var, so stopping and starting again loses nothing.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ -f .env ]; then
  set -a
  source ./.env
  set +a
fi

VAR="$ROOT/var"; LOGS="$VAR/logs"; PIDS="$VAR/pids"
mkdir -p "$LOGS" "$PIDS" "$VAR/temporal"

PORT="${PORT:-8080}"
export WORKFLOW_BACKEND="${WORKFLOW_BACKEND:-temporal}"

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
running() { [ -f "$PIDS/$1.pid" ] && kill -0 "$(cat "$PIDS/$1.pid")" 2>/dev/null; }
start_bg() {  # name, command...
  local name="$1"; shift
  if running "$name"; then say "$name already running (pid $(cat "$PIDS/$name.pid"))"; return; fi
  nohup "$@" >> "$LOGS/$name.log" 2>&1 &
  echo $! > "$PIDS/$name.pid"
  say "started $name (pid $!, log var/logs/$name.log)"
}

# ---- Python environment
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then PY="$candidate"; break; fi
  done
fi
[ -n "$PY" ] || { echo "Python 3.10+ is required (brew install python@3.12)"; exit 1; }
if [ ! -x .venv/bin/python ]; then
  say "creating .venv with $PY"
  "$PY" -m venv .venv
fi
if ! .venv/bin/python -c "import django, temporalio, playwright, rest_framework" 2>/dev/null; then
  say "installing Python packages"
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r backend/requirements.txt
fi
if [ ! -d "${PLAYWRIGHT_BROWSERS_PATH:-$HOME/Library/Caches/ms-playwright}" ] && [ ! -d "$HOME/.cache/ms-playwright" ]; then
  say "installing Chromium for the browser agent"
  .venv/bin/playwright install chromium
fi

# ---- Frontend (build once; rebuild when sources change)
if [ ! -f frontend/dist/index.html ] || [ -n "$(find frontend/src -newer frontend/dist/index.html -print -quit 2>/dev/null)" ]; then
  if command -v npm >/dev/null; then
    say "building the screens"
    (cd frontend && { [ -d node_modules ] || npm install --no-audit --no-fund; } && npm run build >/dev/null)
  else
    echo "npm not found: install Node.js 18+ to build the screens (the API still works)."
  fi
fi

# ---- Database
cd backend
say "preparing the database"
../.venv/bin/python manage.py migrate --noinput -v 0
../.venv/bin/python manage.py backfill_dues
../.venv/bin/python manage.py collectstatic --noinput -v 0
../.venv/bin/python manage.py seed_demo ${SEED_ARGS:-}
cd "$ROOT"

# ---- Temporal + worker
if [ "$WORKFLOW_BACKEND" = "temporal" ]; then
  if command -v temporal >/dev/null; then
    start_bg temporal temporal server start-dev --ip 127.0.0.1 --port 7233 --ui-port 8233 \
      --db-filename "$VAR/temporal/temporal.db" --log-level error
    for _ in $(seq 1 30); do temporal operator cluster health >/dev/null 2>&1 && break; sleep 1; done
    start_bg worker bash -c "cd '$ROOT/backend' && exec ../.venv/bin/python -u manage.py run_worker"
  else
    echo "The Temporal CLI is not installed (brew install temporal). Falling back to WORKFLOW_BACKEND=local."
    export WORKFLOW_BACKEND=local
  fi
fi

# ---- Demo HRMS (a second, different HR tool for the agent)
# Two people whose details HR has not entered yet (they wait in payroll until HR fills them in on /hr/missing).
# Start with MOCK_HRMS_MISSING= (empty) for complete data.
start_bg mock-hrms env MOCK_HRMS_MISSING="${MOCK_HRMS_MISSING-BL004:location,BL012:gender}" .venv/bin/python -u mock_hrms/app.py --port 8100
# The same tool laid out like Keka: lists inside tabs and under Reports, details on each employee's Job tab
start_bg mock-hrms-nested env MOCK_HRMS_LAYOUT=nested MOCK_HRMS_SESSION_PAGES=0 .venv/bin/python -u mock_hrms/app.py --port 8101

# ---- Web app
start_bg web bash -c "cd '$ROOT/backend' && WORKFLOW_BACKEND=$WORKFLOW_BACKEND exec ../.venv/bin/gunicorn config.wsgi:application \
  --bind 127.0.0.1:$PORT --workers 1 --threads 8 --timeout 180 --access-logfile -"
for _ in $(seq 1 30); do curl -sf "http://127.0.0.1:$PORT/api/me" >/dev/null && break; sleep 1; done

cat <<MSG

  Payroll app     http://localhost:$PORT      maker / maker123 (prepares)   checker / checker123 (approves)
  Demo HRMS       http://localhost:8100       hr.admin / demo123   (details still to enter: /hr/missing)
  Keka-like HRMS  http://localhost:8101       hr.admin / demo123   (lists behind menus and tabs)
  Temporal UI     http://localhost:8233       (workflow backend: $WORKFLOW_BACKEND)

  Stop everything:   ./scripts/stop.sh      Restart (state is kept):  ./scripts/stop.sh && ./scripts/start.sh
MSG
