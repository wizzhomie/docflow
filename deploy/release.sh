#!/usr/bin/env bash
# Put the current checkout on the server, or put the previous one back.
#
#   sudo deploy/release.sh            # gate → restart → smoke, roll back on failure
#   sudo deploy/release.sh --check    # gate → smoke, restart nothing
#   sudo deploy/release.sh --rollback # back to the previously released commit
#   sudo deploy/release.sh --force    # release even when nothing changed
#
# The gate is what CI runs: lint, the regression baseline, and both suites in one
# process each with their coverage floor (DocFlow and the assistant). A release
# that fails the gate restarts nothing. A release that passes the gate but fails
# the smoke test is rolled back to the commit recorded by the last successful
# release, and that commit is started and checked before the script gives up.
#
# The script runs as root because it restarts services; git and the tests run as
# the repository owner, so the checkout keeps belonging to them.
set -euo pipefail

DOCFLOW_ROOT="${DOCFLOW_ROOT:-/home/ubuntu/docflow}"
OWNER="${OWNER:-$(stat -c '%U' "$DOCFLOW_ROOT")}"
STATE="$DOCFLOW_ROOT/.run/release"
MODE="${1:-deploy}"
FORCE="${2:-}"
if [ "$MODE" = "--force" ]; then
    MODE="deploy"
    FORCE="--force"
fi

if [ "$(id -u)" -ne 0 ]; then
    echo "run me with sudo (the service is restarted)" >&2
    exit 1
fi

install -d -o "$OWNER" -g "$OWNER" "$STATE"

as_owner() {
    runuser -u "$OWNER" -- "$@"
}

log() {
    printf '%s %s\n' "$(date -Is)" "$*" | as_owner tee -a "$STATE/releases.log" >/dev/null
    printf '%s\n' "$*"
}

head_commit() {
    as_owner git -C "$DOCFLOW_ROOT" rev-parse --short HEAD
}

gate() {
    echo "== gate"
    as_owner bash -c "cd '$DOCFLOW_ROOT' && .venv/bin/python -m ruff check ."
    as_owner bash -c "cd '$DOCFLOW_ROOT' && .venv/bin/python tests/run_eval.py >/dev/null"
    as_owner bash -c "cd '$DOCFLOW_ROOT' && .venv/bin/python -m pytest -q --cov --cov-fail-under=68" \
        || { echo "gate: the DocFlow suite failed"; return 1; }
    as_owner bash -c "cd '$DOCFLOW_ROOT/assistant' && .venv/bin/python -m ruff check ." \
        || { echo "gate: the assistant lint failed"; return 1; }
    as_owner bash -c "cd '$DOCFLOW_ROOT/assistant' && .venv/bin/python -m pytest -q --cov" \
        || { echo "gate: the assistant suite failed"; return 1; }
    echo "gate passed"
}

restart() {
    systemctl restart docflow.service docflow-worker.service
}

wait_ready() {
    for _ in $(seq 1 30); do
        if curl -fsS -o /dev/null -m 2 http://127.0.0.1:8765/api/health; then
            return 0
        fi
        sleep 1
    done
    return 1
}

smoke() {
    echo "== smoke"
    local path code
    wait_ready || { echo "smoke: the service did not answer after 30 s"; return 1; }
    for path in / /app /api/health /api/health/detail /api/metrics /api/stats \
                /api/impact /api/config /api/roadmap /api/session; do
        code="$(curl -s -o /dev/null -m 10 -w '%{http_code}' "http://127.0.0.1:8765$path")"
        if [ "$code" != 200 ]; then
            echo "smoke: $path returned ${code:-nothing}"
            return 1
        fi
    done
    systemctl is-active --quiet docflow-worker \
        || { echo "smoke: docflow-worker is not active"; return 1; }
    curl -s -m 10 http://127.0.0.1:8765/api/health/detail \
        | grep -Eq '"status": *"ok"' \
        || { echo "smoke: /api/health/detail is not ok"; return 1; }
    for path in /api/config /api/health; do
        code="$(curl -s -o /dev/null -m 10 -w '%{http_code}' "http://127.0.0.1:8769$path")"
        if [ "$code" != 200 ]; then
            echo "smoke: the assistant $path returned ${code:-nothing}"
            return 1
        fi
    done
    echo "smoke passed"
}

record() {
    local commit="$1" previous="$2"
    as_owner bash -c "printf '%s\n' '$previous' > '$STATE/previous'"
    as_owner bash -c "printf '%s\n' '$commit' > '$STATE/current'"
}

current="$(head_commit)"
released="$(cat "$STATE/current" 2>/dev/null || true)"
rollback_point="$(cat "$STATE/previous" 2>/dev/null || true)"

# A rollback needs a commit to go back to, not a half-edited tree.
if [ "$MODE" != "--check" ] && [ -n "$(as_owner git -C "$DOCFLOW_ROOT" status --porcelain)" ]; then
    echo "the checkout has uncommitted changes: commit or stash them first" >&2
    exit 1
fi

case "$MODE" in
    --check)
        gate
        smoke
        log "check passed at $current (nothing restarted)"
        ;;
    --rollback)
        if [ -z "$rollback_point" ]; then
            echo "no earlier release to roll back to" >&2
            exit 1
        fi
        log "rolling back from ${released:-unknown} to $rollback_point"
        as_owner git -C "$DOCFLOW_ROOT" switch --detach "$rollback_point"
        restart
        if smoke; then
            as_owner bash -c "printf '%s\n' '$rollback_point' > '$STATE/current'"
            as_owner bash -c "printf '%s\n' '' > '$STATE/previous'"
            log "rollback is serving $rollback_point; run 'git switch main' before the next release"
        else
            log "rollback to $rollback_point did not pass the smoke test: a human is needed"
            exit 1
        fi
        ;;
    deploy)
        if [ "$current" = "$released" ] && [ "$FORCE" != "--force" ]; then
            log "nothing to do: $current is already the released commit"
            exit 0
        fi
        gate || { log "gate failed at $current; nothing was restarted"; exit 1; }
        restart
        if smoke; then
            record "$current" "$released"
            log "released $current (rollback point: ${released:-none})"
        else
            log "smoke failed at $current"
            if [ -n "$released" ]; then
                log "rolling back to the last released commit $released"
                as_owner git -C "$DOCFLOW_ROOT" switch --detach "$released"
                restart
                smoke || { log "rollback also failed: the demo needs a human"; exit 1; }
                log "rolled back to $released; fix and release again"
            fi
            exit 1
        fi
        ;;
    *)
        echo "usage: release.sh [--check|--rollback|--force]" >&2
        exit 2
        ;;
esac
