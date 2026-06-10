#!/usr/bin/env bash
# Render a Caddyfile from a repo template and deploy it to a BFF VM, then reload.
#
# WHY THIS EXISTS:
#   /etc/caddy/Caddyfile on each BFF VM is root-owned and was historically set up
#   by hand, so it is NOT captured by any IaC. After a GCP project / static-IP
#   cutover (e.g. lupin -> benjamin, 2026-05-30) the VM Caddyfile kept pointing at
#   the OLD sslip.io hostname (old IP). Caddy then had no certificate for the NEW
#   SNI and TLS died at the handshake with `tlsv1 alert internal error` (alert 80)
#   — the dev BFF looked "deployed" (gh vars updated) but was unreachable over
#   HTTPS. This script makes the Caddyfile a versioned, redeployable artifact so
#   the breakage stops recurring on every rebuild/cutover.
#
# Usage:
#   deploy/caddy/sync-caddy.sh <ssh-target> <bff-host> <template> [fe-host] [fe-root]
#     ssh-target  SSH destination, e.g. lupin@35.201.239.38
#     bff-host    sslip.io hostname for the new IP,
#                 e.g. pantheon-lupin-dev-bff.35.201.239.38.sslip.io
#     template    repo-relative template, e.g. deploy/caddy/dev.Caddyfile.tmpl
#     fe-host     optional sslip.io hostname for the static dev frontend,
#                 required when template contains __FE_HOST__
#     fe-root     optional static frontend root (default: /var/www/pantheon-dev-fe)
#
# Env:
#   CADDY_SSH_KEY   SSH identity (default: ~/.ssh/google_compute_engine — the
#                   default agent key is rejected by these VMs, use this one)
#
# Idempotent: re-running with the same host is a no-op for Caddy (cert cached).
set -euo pipefail

SSH_TARGET="${1:?ssh-target required, e.g. lupin@35.201.239.38}"
BFF_HOST="${2:?bff-host required, e.g. pantheon-lupin-dev-bff.<ip>.sslip.io}"
TEMPLATE="${3:?template path required, e.g. deploy/caddy/dev.Caddyfile.tmpl}"
FE_HOST="${4:-}"
FE_ROOT="${5:-${PANTHEON_DEV_FE_ROOT:-/var/www/pantheon-dev-fe}}"

SSH_KEY="${CADDY_SSH_KEY:-$HOME/.ssh/google_compute_engine}"
SSH=(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)

[[ -f "$TEMPLATE" ]] || { echo "ERROR: template not found: $TEMPLATE" >&2; exit 1; }
if grep -Eq '__FE_HOST__|__FE_ROOT__' "$TEMPLATE" && [[ -z "$FE_HOST" ]]; then
  echo "ERROR: template requires fe-host; pass [fe-host] [fe-root]" >&2
  exit 1
fi

echo "=== sync-caddy: ${SSH_TARGET}  bff=${BFF_HOST}  tmpl=${TEMPLATE} ==="
if [[ -n "$FE_HOST" ]]; then
  echo "=== sync-caddy: frontend=${FE_HOST} root=${FE_ROOT} ==="
fi

RENDERED="$(sed \
  -e "s|__BFF_HOST__|${BFF_HOST}|g" \
  -e "s|__FE_HOST__|${FE_HOST}|g" \
  -e "s|__FE_ROOT__|${FE_ROOT}|g" \
  "$TEMPLATE")"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

# Push rendered config, back up the existing one, validate, then reload.
printf '%s\n' "$RENDERED" | "${SSH[@]}" "$SSH_TARGET" '
  set -euo pipefail
  tmp="$(mktemp)"
  cat > "$tmp"
  if sudo test -f /etc/caddy/Caddyfile; then
    sudo cp /etc/caddy/Caddyfile "/etc/caddy/Caddyfile.bak.'"$STAMP"'"
  fi
  # install (not mv) so the file lands root:root 0644 — caddy runs as the caddy
  # user and a lupin-owned 0600 file makes `systemctl reload` fail with EACCES.
  sudo install -o root -g root -m 644 "$tmp" /etc/caddy/Caddyfile
  rm -f "$tmp"
  sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
  sudo systemctl reload caddy
  echo "  reloaded; backup: /etc/caddy/Caddyfile.bak.'"$STAMP"'"
'

# Verify HTTPS health from here (ACME issuance for a new SNI takes a few seconds).
echo "=== verifying https://${BFF_HOST}/health ==="
bff_ok=false
for i in $(seq 1 8); do
  code="$(curl -sS -m 8 -o /dev/null -w '%{http_code}' "https://${BFF_HOST}/health" 2>/dev/null || echo 000)"
  echo "  attempt ${i}: http_code=${code}"
  if [[ "$code" == "200" ]]; then
    echo "OK: ${BFF_HOST} live"
    bff_ok=true
    break
  fi
  sleep 5
done
if [[ "$bff_ok" != "true" ]]; then
  echo "WARN: ${BFF_HOST}/health did not return 200 — check 'journalctl -u caddy' for ACME errors" >&2
  exit 1
fi

if [[ -n "$FE_HOST" ]]; then
  echo "=== verifying https://${FE_HOST}/ ==="
  for i in $(seq 1 8); do
    code="$(curl -sS -m 8 -o /dev/null -w '%{http_code}' "https://${FE_HOST}/" 2>/dev/null || echo 000)"
    echo "  attempt ${i}: http_code=${code}"
    [[ "$code" == "200" ]] && { echo "OK: ${FE_HOST} live"; exit 0; }
    sleep 5
  done
  echo "WARN: ${FE_HOST}/ did not return 200 — check FE root ${FE_ROOT} and 'journalctl -u caddy'" >&2
  exit 1
fi

exit 0
