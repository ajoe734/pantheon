#!/usr/bin/env bash
# Pantheon production control VM bootstrap (ENV-PROD-CONTROL-IMPL-001).
#
# Prepares the on-VM tree for the FIRST release. It does not invent a healthy release: it
# installs a 503 upstream placeholder and no `fe/current`, so the first switch must run with
# PROD_BOOTSTRAP_FIRST_RELEASE=true and has no rollback target. It NEVER formats, partitions
# or mounts over the production data disk; an unmounted or unformatted disk is a hard failure.
#
# Required env: PROD_ROOT PROD_DATA_MOUNT PROD_BFF_HOST PROD_FE_HOST PROD_IDP_ISSUER
#   PROD_SECRETS_ENV_FILE PROD_POSTGRES_PASSWORD_FILE PROD_COMPOSE_ENV_FILE
# Optional: DOCKER_BIN (default docker), PROD_BOOTSTRAP_SKIP_STATEFUL=true (render tree only).

set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKER="${DOCKER_BIN:-docker}"

fail() { printf '{"status":"error","error_code":"%s","message":"%s"}\n' "$1" "$2" >&2; exit 1; }
for v in PROD_ROOT PROD_DATA_MOUNT PROD_BFF_HOST PROD_FE_HOST PROD_IDP_ISSUER \
         PROD_SECRETS_ENV_FILE PROD_POSTGRES_PASSWORD_FILE PROD_COMPOSE_ENV_FILE; do
  [[ -n "${!v:-}" ]] || fail MISSING_INPUT "required env ${v} is missing"
done

mountpoint -q "$PROD_DATA_MOUNT" || fail DATA_DISK_NOT_MOUNTED "${PROD_DATA_MOUNT} is not mounted; bootstrap never formats or mounts the data disk"
for f in "$PROD_SECRETS_ENV_FILE" "$PROD_POSTGRES_PASSWORD_FILE"; do
  [[ -s "$f" ]] || fail SECRET_MISSING "secret file ${f} missing or empty (supplied from PROD_SECRETS_REF, never generated here)"
  [[ "$(stat -c '%a' "$f")" =~ ^(400|600)$ ]] || fail SECRET_MODE "${f} must be mode 0400/0600"
done
[[ "$PROD_FE_HOST" != "$PROD_BFF_HOST" ]] || fail HOST_COLLISION "FE and BFF hosts must differ"
[[ "$PROD_IDP_ISSUER" == https://* ]] || fail IDP_NOT_HTTPS "IdP issuer must be https"

umask 077
mkdir -p "${PROD_ROOT}"/{state/journal,state/snapshots,fe/releases,proxy,config} \
         "${PROD_DATA_MOUNT}/postgres" "${PROD_DATA_MOUNT}/nats" || fail MKDIR "cannot create tree"

# Placeholder upstream: explicit 503 until the first controller switch replaces it.
if [[ ! -e "${PROD_ROOT}/proxy/bff_upstream.caddy" ]]; then
  printf 'respond "no production release active" 503\n' > "${PROD_ROOT}/proxy/bff_upstream.caddy"
fi

# External same-origin runtime config, outside the sealed FE release dir (served no-store).
tmp="${PROD_ROOT}/config/runtime-config.js.new.$$"
python3 - "$PROD_BFF_HOST" "$PROD_IDP_ISSUER" > "$tmp" <<'PY' || fail RENDER "runtime-config render failed"
import json, sys
cfg = {"environment": "production", "bffBaseUrl": f"https://{sys.argv[1]}", "oidcIssuer": sys.argv[2]}
print("window.__PANTHEON_RUNTIME_CONFIG__ = Object.freeze(" + json.dumps(cfg, sort_keys=True) + ");")
PY
chmod 0644 "$tmp"; mv -f "$tmp" "${PROD_ROOT}/config/runtime-config.js" || fail RENDER "cannot install runtime-config.js"

if [[ "${PROD_BOOTSTRAP_SKIP_STATEFUL:-false}" != "true" ]]; then
  "$DOCKER" compose -p pantheon-production -f "${SCRIPT_DIR}/docker-compose.prod-control.yml" \
    --env-file "$PROD_COMPOSE_ENV_FILE" --profile stateful up -d --no-build \
    || fail STATEFUL_START "stateful services failed to start"
fi
printf '{"status":"bootstrapped","first_release_requires":"PROD_BOOTSTRAP_FIRST_RELEASE=true","rollback_target":null}\n'
