#!/usr/bin/env bash
# Stop every service started by start.sh. All state stays in ./var.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
for name in web worker mock-hrms mock-hrms-nested temporal; do
  pidfile="$ROOT/var/pids/$name.pid"
  if [ -f "$pidfile" ]; then
    pid="$(cat "$pidfile")"
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null
      for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.25; done
      kill -9 "$pid" 2>/dev/null || true
      echo "stopped $name"
    fi
    rm -f "$pidfile"
  fi
done
