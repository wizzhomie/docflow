#!/usr/bin/env bash
# Copy the newest local backup bundle off the machine.
#
#   deploy/backup/offsite.sh          # push the newest bundle and apply retention
#   deploy/backup/offsite.sh --check  # restore the newest snapshot and verify sums
#
# The local backup protects against a mistake; this one protects against losing
# the disk. `restic` encrypts everything before it leaves, so the B2 credentials
# grant storage only — the repository password, kept in the same 600 env file,
# is what decrypts. Losing it means losing the copy.
set -euo pipefail

ENV_FILE="${DOCFLOW_BACKUP_ENV:-$HOME/.config/docflow/backup.env}"
DEST="${DOCFLOW_BACKUP_DIR:-/home/ubuntu/backups/docflow}"
KEEP="${DOCFLOW_OFFSITE_KEEP:-14}"
HOST="$(hostname)"

if [ ! -r "$ENV_FILE" ]; then
    echo "missing $ENV_FILE (B2 credentials and the repository password)" >&2
    exit 1
fi
# shellcheck disable=SC1090
set -a
. "$ENV_FILE"
set +a

if ! command -v restic >/dev/null 2>&1; then
    echo "restic is not installed" >&2
    exit 1
fi

newest() {
    find "$DEST" -mindepth 1 -maxdepth 1 -type d -name '20*' 2>/dev/null | sort | tail -1
}

check_restore() {
    local tmp bundle
    tmp="$(mktemp -d)"
    trap 'rm -rf "${tmp:-}"' EXIT
    restic restore latest --target "$tmp" >/dev/null
    bundle="$(find "$tmp" -type f -name SHA256SUMS -printf '%h\n' 2>/dev/null | sort | tail -1)"
    if [ -z "$bundle" ]; then
        echo "the newest snapshot has no SHA256SUMS" >&2
        return 1
    fi
    ( cd "$bundle" && sha256sum --check --quiet SHA256SUMS )
    echo "restored and verified: ${bundle#"$tmp"/}"
}

if [ "${1:-}" = "--check" ]; then
    check_restore
    echo "== repository check"
    restic check --read-data-subset=10%
    exit 0
fi

latest="$(newest)"
if [ -z "$latest" ]; then
    echo "no local backup in $DEST" >&2
    exit 1
fi

echo "== pushing $(basename "$latest")"
restic backup --tag docflow --host "$HOST" "$latest"
restic forget --keep-last "$KEEP" --keep-daily "$KEEP" --keep-weekly 4 --prune

# Sunday: read a slice back. A repository nobody verifies is a hope, not a copy.
if [ "$(date +%u)" = "7" ]; then
    echo "== weekly repository check"
    restic check --read-data-subset=10%
fi
