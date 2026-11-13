#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8765}"
RUN_DIR="$DIR/.run"
PIDFILE="$RUN_DIR/docflow.pid"
LOG="$RUN_DIR/docflow.log"

if [[ -f "$DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$DIR/.env"
  set +a
fi

mkdir -p "$RUN_DIR"

running() {
  [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null
}

# True while nothing else holds the configured address. A restart right after
# stop() used to hit EADDRINUSE, because uvicorn's reloader child releases the
# socket a moment after the parent exits.
port_free() {
  python3 - "$HOST" "$PORT" <<'PY' 2>/dev/null
import socket
import sys

host, port = sys.argv[1], int(sys.argv[2])
probe = socket.socket()
probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    probe.bind(("0.0.0.0" if host in {"0.0.0.0", "::"} else host, port))
except OSError:
    sys.exit(1)
finally:
    probe.close()
PY
}

wait_free() {
  local deadline=$((SECONDS + 15))
  while ! port_free; do
    if (( SECONDS >= deadline )); then
      echo "Port $PORT is still busy after 15s; is another process using it?" >&2
      return 1
    fi
    sleep 0.3
  done
}

start() {
  if running; then
    echo "DocFlow already running (pid $(cat "$PIDFILE"))"
    return 0
  fi
  wait_free
  cd "$DIR"
  setsid python3 -m uvicorn app:app --host "$HOST" --port "$PORT" --reload \
    </dev/null >"$LOG" 2>&1 &
  echo $! >"$PIDFILE"
  sleep 2
  if running && curl -fsS "http://$HOST:$PORT/api/health" >/dev/null 2>&1; then
    echo "DocFlow started (pid $(cat "$PIDFILE")) -> http://$HOST:$PORT"
  else
    echo "Failed to start. Logs:"; tail -n 30 "$LOG"; return 1
  fi
}

stop() {
  if ! running; then
    echo "DocFlow is not running"
    rm -f "$PIDFILE"
    return 0
  fi
  local pid; pid="$(cat "$PIDFILE")"
  kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
  rm -f "$PIDFILE"
  local deadline=$((SECONDS + 15))
  while ! port_free; do
    (( SECONDS >= deadline )) && break
    sleep 0.3
  done
  echo "DocFlow stopped"
}

status() {
  if running; then
    echo "running (pid $(cat "$PIDFILE")) on http://$HOST:$PORT"
    curl -fsS "http://$HOST:$PORT/api/health" || true; echo
  else
    echo "stopped"
  fi
}

case "${1:-}" in
  start)   start ;;
  stop)    stop ;;
  restart) stop; start ;;
  status)  status ;;
  logs)    tail -n 50 -f "$LOG" ;;
  *) echo "usage: $0 {start|stop|restart|status|logs}"; exit 1 ;;
esac
