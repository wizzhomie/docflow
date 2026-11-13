#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8765}"
RUN_DIR="$DIR/.run"
PIDFILE="$RUN_DIR/docflow.pid"
LOG="$RUN_DIR/docflow.log"

mkdir -p "$RUN_DIR"

running() {
  [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null
}

start() {
  if running; then
    echo "DocFlow already running (pid $(cat "$PIDFILE"))"
    return 0
  fi
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
  sleep 1
  rm -f "$PIDFILE"
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
