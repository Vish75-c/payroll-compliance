#!/usr/bin/env bash
# Install and run Horilla locally (no Docker), with Brickline's made-up data entered.
#   ./scripts/horilla.sh setup        clone Horilla into var/horilla, install, create admin/admin, enter data as of June
#   ./scripts/horilla.sh start        run Horilla on http://localhost:8000
#   ./scripts/horilla.sh stage july   HR enters Priya's raise (as happened in July)
#   ./scripts/horilla.sh stage august|september|october
#                                     HR's next month: people joining and leaving, a raise approved late
#                                     (arrears), leave and attendance. Run them in order, one per payroll month.
#   BRICKLINE_KEEP=BL001,...,BL010 ./scripts/horilla.sh stage october
#                                     keep only those people (the others are removed from Horilla)
#   ./scripts/horilla.sh stop
# Tested with Horilla master of Nov 2026 on Python 3.12 (SQLite, no Redis).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
H="$ROOT/var/horilla"
PY="${PYTHON:-python3.12}"
# host.docker.internal: the payroll app running in Docker (docker compose) reaches Horilla by that name.
export DEBUG=1 SECRET_KEY="${HORILLA_SECRET_KEY:-local-demo-only}" ALLOWED_HOSTS=localhost,127.0.0.1,host.docker.internal \
       CSRF_TRUSTED_ORIGINS=http://localhost:8000,http://127.0.0.1:8000,http://host.docker.internal:8000
# Address Horilla listens on. 127.0.0.1 is enough for ./scripts/start.sh and for Docker Desktop (macOS/Windows);
# with Docker on Linux use HORILLA_BIND=0.0.0.0 so containers can reach it (it is then open to your network).
BIND="${HORILLA_BIND:-127.0.0.1}"
unset DJANGO_SETTINGS_MODULE
export BRICKLINE_SEED="$ROOT/data/brickline/seed.json"  # the same people as the spreadsheet and the Demo HRMS
export BRICKLINE_STAGE_FILE="$H/stage"                   # which month Horilla's data has reached (read by the tests)
export BRICKLINE_PEOPLE_FILE="$H/people"                 # who is in Horilla, when BRICKLINE_KEEP limits it (read by the tests)
manage() { (cd "$H/src" && "$H/venv/bin/python" manage.py "$@"); }

case "${1:-}" in
  setup)
    mkdir -p "$H"
    [ -d "$H/src" ] || git clone --depth 1 https://github.com/horilla-opensource/horilla.git "$H/src"
    [ -x "$H/venv/bin/python" ] || "$PY" -m venv "$H/venv"
    "$H/venv/bin/pip" install -q --upgrade pip
    "$H/venv/bin/pip" install -q -r "$H/src/requirements.txt"
    manage migrate --noinput
    DJANGO_SUPERUSER_PASSWORD=admin manage createsuperuser --noinput --username admin --email admin@brickline.example 2>/dev/null || true
    BRICKLINE_STAGE=june manage shell < "$ROOT/integrations/horilla/seed_brickline.py"
    echo "Horilla is ready. Start it with: ./scripts/horilla.sh start   (login admin / admin)"
    ;;
  start)
    mkdir -p "$ROOT/var/logs" "$ROOT/var/pids"
    # exec: the recorded pid is Horilla itself, not a wrapper shell (stopping a wrapper left Horilla running).
    (cd "$H/src" && exec nohup "$H/venv/bin/python" manage.py runserver "$BIND:8000" --noreload >> "$ROOT/var/logs/horilla.log" 2>&1) &
    echo $! > "$ROOT/var/pids/horilla.pid"
    echo "Horilla starting on http://localhost:8000 (listening on $BIND; admin / admin); log: var/logs/horilla.log"
    ;;
  stage)
    BRICKLINE_STAGE="${2:-july}" manage shell < "$ROOT/integrations/horilla/seed_brickline.py"
    ;;
  stop)
    if [ -f "$ROOT/var/pids/horilla.pid" ]; then
      pid="$(cat "$ROOT/var/pids/horilla.pid")"
      pkill -TERM -P "$pid" 2>/dev/null || true  # anything it started
      kill "$pid" 2>/dev/null && echo "stopped Horilla" || true
      rm -f "$ROOT/var/pids/horilla.pid"
    fi
    ;;
  *) echo "usage: $0 setup | start | stage june|july|august|september|october | stop"; exit 1 ;;
esac
