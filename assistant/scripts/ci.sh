#!/usr/bin/env bash
# One command for every check that must pass before a change is called done:
# lint, offline tests and the coverage gate. The live smoke is opt-in, because CI has
# no DocFlow and no model:
#
#   scripts/ci.sh          # lint + tests + coverage
#   scripts/ci.sh --live   # also talk to the real services (deploy them first)
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${OPERATOR_PYTHON:-.venv/bin/python}"
if [ ! -x "$PYTHON" ]; then
  PYTHON="$(command -v python3)"
fi
RUFF="${OPERATOR_RUFF:-.venv/bin/ruff}"
if [ ! -x "$RUFF" ]; then
  RUFF="$(command -v ruff)"
fi

echo "== lint"
"$RUFF" check .

echo "== tests and coverage"
"$PYTHON" -m pytest --cov --cov-report=term-missing

if [ "${1:-}" = "--live" ]; then
  echo "== live smoke"
  "$PYTHON" -m pytest -m live
fi
