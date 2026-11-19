#!/usr/bin/env bash
# Start the operator web app on 127.0.0.1:8769 (DocFlow stays on 8765).
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${OPERATOR_PYTHON:-.venv/bin/python}"
PORT="${OPERATOR_PORT:-8769}"
HOST="${OPERATOR_HOST:-0.0.0.0}"

exec "$PYTHON" -m uvicorn docflow_operator.server:app --host "$HOST" --port "$PORT"
