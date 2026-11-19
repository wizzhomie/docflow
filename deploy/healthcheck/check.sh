#!/usr/bin/env bash
# Says whether the demo is actually serving, from the outside as well as locally.
#
#   deploy/healthcheck/check.sh
#
# Exit 0 when every check answers; otherwise the failures go to
# ~/docflow/.run/health.log and, when SENTRY_DSN is configured, to GlitchTip as
# one error event — the same tracker the application errors land in, so a failed
# check and a crash are read in one place.
#
# When DOCFLOW_PING_URL is set, every run also pings an external dead-man switch
# (healthy run: the base URL, failed run: /fail). That is what reports a machine
# that is down: its own timer cannot.
#
# A single probe is not a verdict: a deploy restarts the service and the check can
# land in that one-second gap, which paged an owner for a healthy site. The probe
# runs again a few seconds later, and only a failure that survives every attempt
# is reported.
set -uo pipefail

ROOT="${DOCFLOW_ROOT:-/home/ubuntu/docflow}"
LOG="$ROOT/.run/health.log"
STATE="$ROOT/.run/health.state"
PYTHON="${DOCFLOW_PYTHON:-$ROOT/.venv/bin/python}"

problems=()

check_url() {
    local name="$1" url="$2" want="$3" code
    code="$(curl -fsS -o /dev/null -m 10 -w '%{http_code}' "$url" 2>/dev/null || true)"
    if [ "$code" != "$want" ]; then
        problems+=("$name: HTTP ${code:-none} from $url (wanted $want)")
    fi
}

# The public names go through Cloudflare: resolve them with a public resolver and pin the
# address, because the VCN resolver can cache a stale answer for hours and the tunnel would
# then look broken while it is fine.
check_public() {
    local name="$1" host="$2" want="$3" code ip
    ip="$(dig +short A "$host" @1.1.1.1 2>/dev/null | head -1 || true)"
    if [ -n "$ip" ]; then
        code="$(curl -fsS -o /dev/null -m 10 -w '%{http_code}' --resolve "$host:443:$ip"             "https://$host/api/health" 2>/dev/null || true)"
    else
        code="$(curl -fsS -o /dev/null -m 10 -w '%{http_code}' "https://$host/api/health"             2>/dev/null || true)"
    fi
    if [ "$code" != "$want" ]; then
        problems+=("$name: HTTP ${code:-none} from https://$host/api/health (wanted $want)")
    fi
}

LOCAL="${DOCFLOW_LOCAL_URL:-http://127.0.0.1:8765}/api/health"
RETRIES="${DOCFLOW_HEALTH_RETRIES:-3}"
DELAY="${DOCFLOW_HEALTH_DELAY:-5}"

probe() {
    problems=()
    check_url docflow-local "$LOCAL" 200
    check_public docflow-public docflow.website 200
    check_public operator-public chat.docflow.website 200

    for unit in docflow docflow-worker docflow-operator docflow-operator-model; do
        systemctl is-active --quiet "$unit" || problems+=("service $unit is not active")
    done

    pg_isready -q || problems+=("PostgreSQL does not accept connections")
}

attempt=1
probe
while [ "${#problems[@]}" -gt 0 ] && [ "$attempt" -lt "$RETRIES" ]; do
    sleep "$DELAY"
    attempt=$((attempt + 1))
    probe
done
if [ "${#problems[@]}" -eq 0 ] && [ "$attempt" -gt 1 ]; then
    printf '%s healthcheck recovered on attempt %s of %s\n' "$(date -Is)" "$attempt" "$RETRIES" \
        >> "$LOG"
fi

ping_switch() {
    command -v python3 >/dev/null 2>&1 || return 0
    python3 "$(dirname "$0")/ping.py" "$1" || true
}

# Mail is a convenience channel: without SMTP settings it does nothing, and its
# own failures never change the verdict of the check.
notify_mail() {
    [ -x "$PYTHON" ] || return 0
    "$PYTHON" "$(dirname "$0")/notify_mail.py" "$1" "$2" || true
}

previous="$(cat "$STATE" 2>/dev/null || echo ok)"

if [ "${#problems[@]}" -eq 0 ]; then
    if [ "$previous" = fail ]; then
        notify_mail ok "the demo answers again"
        printf '%s healthcheck recovered\n' "$(date -Is)" >> "$LOG"
    fi
    printf 'ok\n' > "$STATE"
    ping_switch success
    exit 0
fi

line="$(date -Is) healthcheck failed: $(IFS='; '; echo "${problems[*]}")"
printf '%s\n' "$line" >> "$LOG"
printf '%s\n' "$line" >&2

if [ -n "${SENTRY_DSN:-}" ] && [ -x "$PYTHON" ]; then
    "$PYTHON" "$(dirname "$0")/report.py" "$line" || true
fi
# One mail per outage, not one per five-minute check; the recovery mail closes it.
if [ "$previous" != fail ]; then
    notify_mail fail "$line"
fi
printf 'fail\n' > "$STATE"
ping_switch fail
exit 1
