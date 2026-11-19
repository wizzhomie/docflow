#!/usr/bin/env bash
# Thin wrapper so the deployment command reads like a deployment command:
#
#   scripts/deploy.sh status
#   scripts/deploy.sh deploy
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${OPERATOR_PYTHON:-.venv/bin/python}"
if [ ! -x "$PYTHON" ]; then
  PYTHON="$(command -v python3)"
fi

exec "$PYTHON" scripts/svc.py "$@"
