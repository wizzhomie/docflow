#!/usr/bin/env bash
# Install the demo stack as systemd units on a real server (this is how the Oracle
# machine runs it, instead of the .run/ supervisor used inside the dev container).
#
#   sudo deploy/systemd/install.sh
#
# Paths and the run user can be overridden (the assistant and the deployment
# directory default to subdirectories of the checkout):
#   sudo DOCFLOW_ROOT=/srv/docflow ASSISTANT_ROOT=/srv/docflow/assistant OWNER=ubuntu deploy/systemd/install.sh
#
# The script renders every template, installs the logrotate rule, then enables the
# services and the timers (the nightly backup and the healthcheck). Services whose
# unit file changed are restarted; the DSH web UI is never restarted from here,
# because that is the terminal the operator is watching this from.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCFLOW_ROOT="${DOCFLOW_ROOT:-/home/ubuntu/docflow}"
ASSISTANT_ROOT="${ASSISTANT_ROOT:-$DOCFLOW_ROOT/assistant}"
DEPLOY_ROOT="${DEPLOY_ROOT:-$DOCFLOW_ROOT/deploy}"
OWNER="${OWNER:-$(stat -c '%U' "$DOCFLOW_ROOT")}"

# The Web UI is served through Tailscale Funnel, so the authority it advertises
# must be the tailnet name, not the machine's short hostname: after a restart the
# browser would otherwise be told to trust `https://docflow:10000`, which is not
# the address it opened. Prefer an explicit DSH_PUBLIC_HOST, then the tailnet name
# Tailscale reports, then the hostname.
public_host() {
  if [ -n "${DSH_PUBLIC_HOST:-}" ]; then
    printf '%s' "$DSH_PUBLIC_HOST"
    return
  fi
  local name=""
  if command -v tailscale >/dev/null 2>&1; then
    name="$(tailscale status --json 2>/dev/null \
      | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null \
      || true)"
  fi
  printf '%s' "${name:-$(hostname)}"
}
DSH_HOST="$(public_host)"
# Public access is the owner's domain through a Cloudflare tunnel; the demo embeds the
# widget from its chat subdomain, so the demo (and its www alias) are the allowed origins.
SITE_DOMAIN="${DOCFLOW_SITE_DOMAIN:-docflow.website}"
# The owner's domain is canonical; the tailnet funnel stays a working fallback, so its
# two public names are allowed as well.
CORS_ORIGINS="${OPERATOR_CORS_ORIGINS:-https://$SITE_DOMAIN,https://www.$SITE_DOMAIN,https://$DSH_HOST,https://$DSH_HOST:8443}"
DEMO_URL="${OPERATOR_DEMO_URL:-https://$SITE_DOMAIN/}"

SERVICES=(docflow docflow-worker docflow-operator-model docflow-operator)
RESTART=(docflow docflow-worker docflow-operator-model docflow-operator)
TIMERS=(docflow-backup docflow-healthcheck docflow-offsite)
# One-shot units run from their timer; installing them is enough.
ONESHOT=(docflow-backup docflow-healthcheck docflow-offsite)
if command -v dsh >/dev/null 2>&1; then
  SERVICES+=(dsh-web)
fi

if [ "$(id -u)" -ne 0 ]; then
  echo "run me with sudo" >&2
  exit 1
fi
for root in "$DOCFLOW_ROOT" "$ASSISTANT_ROOT" "$DEPLOY_ROOT"; do
  if [ ! -d "$root" ]; then
    echo "missing directory: $root" >&2
    exit 1
  fi
done
mkdir -p "$DOCFLOW_ROOT/.run" "$ASSISTANT_ROOT/.run/logs"
if [ -d "/home/$OWNER/.dsh" ]; then
  mkdir -p "/home/$OWNER/.dsh/logs"
fi

# The backup reads every tenant, so it runs as a role that is allowed to: read
# only, no login, assumed by the backup script alone. Created once, then granted.
DB_DSN="${DOCFLOW_PG_DSN:-dbname=docflow_dev}"
if sudo -u "$OWNER" psql -d "$DB_DSN" -tAc 'select 1' >/dev/null 2>&1; then
  echo "== backup role"
  if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname = 'docflow_backup'" | grep -qx 1; then
    sudo -u postgres psql -c "CREATE ROLE docflow_backup NOLOGIN BYPASSRLS"
  fi
  sudo -u postgres psql -c "GRANT docflow_backup TO $OWNER"
  sudo -u "$OWNER" psql -d "$DB_DSN" -q -v ON_ERROR_STOP=1 -c "
    GRANT SELECT ON ALL TABLES IN SCHEMA public TO docflow_backup;
    GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO docflow_backup;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO docflow_backup;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON SEQUENCES TO docflow_backup;"
else
  echo "warning: $DB_DSN is not reachable; backup role not configured" >&2
fi

render() {
  sed -e "s|__USER__|$OWNER|g" \
      -e "s|__USER_HOME__|/home/$OWNER|g" \
      -e "s|__DOCFLOW_ROOT__|$DOCFLOW_ROOT|g" \
      -e "s|__ASSISTANT_ROOT__|$ASSISTANT_ROOT|g" \
      -e "s|__DEPLOY_ROOT__|$DEPLOY_ROOT|g" \
      -e "s|__DshHost__|$DSH_HOST|g" \
      -e "s|__CORS_ORIGINS__|$CORS_ORIGINS|g" \
      -e "s|__DEMO_URL__|$DEMO_URL|g" \
      -e "s|__DshWorkdir__|${DSH_WORKDIR:-/home/$OWNER}|g" \
      "$1" > "$2"
}

for unit in "${SERVICES[@]}" "${ONESHOT[@]}"; do
  render "$HERE/$unit.service.in" "/etc/systemd/system/$unit.service"
  echo "installed /etc/systemd/system/$unit.service"
done
for unit in "${TIMERS[@]}"; do
  render "$HERE/$unit.timer.in" "/etc/systemd/system/$unit.timer"
  echo "installed /etc/systemd/system/$unit.timer"
done

render "$HERE/../logrotate/docflow" /etc/logrotate.d/docflow
chmod 0644 /etc/logrotate.d/docflow
echo "installed /etc/logrotate.d/docflow"

# fail2ban is a one-time apt package, not something this installer pulls in; the
# jail policy is ours and is kept in the repo.
if command -v fail2ban-client >/dev/null 2>&1; then
  if [ -d /etc/fail2ban/jail.d ]; then
    install -m 0644 "$HERE/../fail2ban/docflow.local" /etc/fail2ban/jail.d/docflow.local
    systemctl restart fail2ban || true
    echo "installed /etc/fail2ban/jail.d/docflow.local"
  fi
else
  echo "note: fail2ban is not installed; the sshd jail policy was skipped" >&2
fi

systemctl daemon-reload
systemctl enable --now "${SERVICES[@]/%/.service}" "${TIMERS[@]/%/.timer}"
systemctl try-restart "${RESTART[@]/%/.service}" || true
systemctl --no-pager --lines=0 status \
  "${SERVICES[@]/%/.service}" "${TIMERS[@]/%/.timer}" || true
echo "done. check: systemctl status docflow-operator docflow-worker"
