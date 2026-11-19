#!/usr/bin/env bash
# A copy of what cannot be rebuilt: the database, the uploaded documents, the
# confirmed history and the environment file.
#
#   deploy/backup/backup.sh          take a backup and prune the old ones
#   deploy/backup/backup.sh --check  restore the newest backup into a scratch
#                                    database and compare the counts
#
# The copies land in $DOCFLOW_BACKUP_DIR (default ~/backups/docflow), one
# directory per run, 600. This protects against a mistake, a bad migration and a
# corrupted file — it does NOT protect against losing the disk: there is no
# second machine to copy to, and the environment file is deliberately kept out
# of git. A copy on another host is the next step and needs a destination.
set -euo pipefail

ROOT="${DOCFLOW_ROOT:-/home/ubuntu/docflow}"
DEST="${DOCFLOW_BACKUP_DIR:-/home/ubuntu/backups/docflow}"
KEEP="${DOCFLOW_BACKUP_KEEP:-14}"
DSN="${DOCFLOW_PG_DSN:-dbname=docflow_dev}"
SCRATCH_DB="${DOCFLOW_RESTORE_DB:-docflow_restore_check}"
# Row-level security is forced on the working tables, so the application role
# cannot read another tenant even by accident — which is exactly what a dump has
# to do. `docflow_backup` is that exception: read-only, no login of its own, and
# assumed only inside this script.
ROLE="${DOCFLOW_BACKUP_ROLE:-docflow_backup}"

umask 077

newest() {
    find "$DEST" -mindepth 1 -maxdepth 1 -type d -name '20*' 2>/dev/null | sort | tail -1
}

if [ "${1:-}" = "--check" ]; then
    latest="$(newest)"
    if [ -z "$latest" ]; then
        echo "no backup to check in $DEST" >&2
        exit 1
    fi
    echo "checking $latest"
    ( cd "$latest" && sha256sum --check --quiet SHA256SUMS )

    dropdb --if-exists "$SCRATCH_DB"
    createdb "$SCRATCH_DB"
    trap 'dropdb --if-exists "$SCRATCH_DB"' EXIT
    pg_restore --no-owner --no-privileges -d "$SCRATCH_DB" "$latest/db.dump"

    # The counts are read where row-level security does not apply: companies and
    # the counters. A signed-in query would see one tenant and pass anyway.
    restored="$(psql -d "$SCRATCH_DB" -tAc \
        'SELECT (SELECT count(*) FROM companies), (SELECT coalesce(sum(documents), 0) FROM company_stats)')"
    source_counts="$(psql -d "$DSN" -tAc \
        'SELECT (SELECT count(*) FROM companies), (SELECT coalesce(sum(documents), 0) FROM company_stats)')"
    echo "restored companies, documents: $restored"
    echo "source   companies, documents: $source_counts"
    if [ "$restored" != "$source_counts" ]; then
        echo "the restored copy does not match the source" >&2
        exit 1
    fi
    echo "restore check passed"
    exit 0
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
DIR="$DEST/$STAMP"
mkdir -p "$DIR"

if ! psql -d "$DSN" -tAc "SELECT 1 FROM pg_roles WHERE rolname = '$ROLE'" | grep -qx 1; then
    echo "missing role $ROLE: run deploy/systemd/install.sh once, it creates it" >&2
    exit 1
fi
PGOPTIONS="${PGOPTIONS:-} -c role=$ROLE" pg_dump -Fc -d "$DSN" -f "$DIR/db.dump"
tar -czf "$DIR/files.tgz" -C "$ROOT" \
    data/uploads data/confirmed.jsonl data/pending 2>/dev/null \
    || tar -czf "$DIR/files.tgz" -C "$ROOT" data/uploads data/confirmed.jsonl
cp -p "$ROOT/.env" "$DIR/env"
( cd "$DIR" && sha256sum db.dump files.tgz env > SHA256SUMS )

echo "backup written: $DIR ($(du -sh "$DIR" | cut -f1))"

pruned=0
while IFS= read -r old; do
    [ -n "$old" ] || continue
    rm -rf "$DEST/$old"
    pruned=$((pruned + 1))
done < <(find "$DEST" -mindepth 1 -maxdepth 1 -type d -name '20*' | sort | head -n -"$KEEP")
[ "$pruned" -eq 0 ] || echo "pruned $pruned old backup(s), keeping $KEEP"
