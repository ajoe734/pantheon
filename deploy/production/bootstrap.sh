#!/usr/bin/env bash
# Pantheon production control VM bootstrap (ENV-PROD-CONTROL-IMPL-001).
#
# Prepares the on-VM tree for the FIRST release (and is idempotent before subsequent releases).
# It does not invent a healthy release: when no release is active it installs a 503 upstream
# placeholder and no `fe/current`, so the first switch must run with
# PROD_BOOTSTRAP_FIRST_RELEASE=true and has no rollback target. It NEVER formats, partitions
# or attaches over the production data disk; an unmounted disk is a hard failure.
# Pre-acquires only immutable digest-pinned (@sha256:...) images; never builds images.
#
# Required env: PROD_ROOT PROD_DATA_MOUNT PROD_BFF_HOST PROD_FE_HOST PROD_IDP_ISSUER
#   PROD_SECRETS_ENV_FILE PROD_POSTGRES_PASSWORD_FILE PROD_COMPOSE_ENV_FILE
# Optional: DOCKER_BIN (default docker), CURL_BIN (default curl), MOUNTPOINT_BIN (default mountpoint),
#   SUDO_BIN (default sudo), PROD_USE_SUDO=true (OS Login non-root mode),
#   PROD_ALLOW_APT_INSTALL=true (install missing Debian 12 runtime packages),
#   RELEASE_MANIFEST_PATH (pre-pull digest-pinned candidate + baseline images),
#   PROD_BOOTSTRAP_SKIP_STATEFUL=true (render tree only).

set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKER="${DOCKER_BIN:-docker}"
CURL="${CURL_BIN:-curl}"
MOUNTPOINT="${MOUNTPOINT_BIN:-mountpoint}"
SUDO="${SUDO_BIN:-sudo}"

fail() { printf '{"status":"error","error_code":"%s","message":"%s"}\n' "$1" "$2" >&2; exit 1; }

run_priv() {
  if [[ "${PROD_USE_SUDO:-false}" == "true" || "${FAKE_OSLOGIN_NONROOT:-false}" == "true" ]]; then
    "$SUDO" "$@"
  else
    "$@"
  fi
}

for v in PROD_ROOT PROD_DATA_MOUNT PROD_BFF_HOST PROD_FE_HOST PROD_IDP_ISSUER \
         PROD_SECRETS_ENV_FILE PROD_POSTGRES_PASSWORD_FILE PROD_COMPOSE_ENV_FILE; do
  [[ -n "${!v:-}" ]] || fail MISSING_INPUT "required env ${v} is missing"
done

check_runtime_binaries() {
  local b
  for b in "$DOCKER" "$CURL" "$MOUNTPOINT" python3; do
    command -v "$b" >/dev/null 2>&1 || return 1
  done
  return 0
}

if ! check_runtime_binaries; then
  if [[ "${PROD_ALLOW_APT_INSTALL:-false}" == "true" ]] && command -v apt-get >/dev/null 2>&1; then
    run_priv env DEBIAN_FRONTEND=noninteractive apt-get update -y >&2 \
      || fail APT_UPDATE_FAILED "apt-get update failed on bare Debian host"
    run_priv env DEBIAN_FRONTEND=noninteractive apt-get install -y \
      ca-certificates curl gnupg caddy docker.io docker-compose-plugin util-linux python3 >&2 \
      || fail APT_INSTALL_FAILED "apt-get install of bare-Debian runtime packages failed"
  fi
fi
check_runtime_binaries || fail RUNTIME_BINARY_MISSING "required runtime binaries (docker, curl, mountpoint, python3) are missing"

run_priv "$MOUNTPOINT" -q "$PROD_DATA_MOUNT" \
  || fail DATA_DISK_NOT_MOUNTED "${PROD_DATA_MOUNT} is not a mounted data disk; bootstrap never formats or attaches disks"

for f in "$PROD_SECRETS_ENV_FILE" "$PROD_POSTGRES_PASSWORD_FILE"; do
  run_priv test -s "$f" || fail SECRET_MISSING "secret file ${f} missing or empty (supplied from PROD_SECRETS_REF, never generated here)"
  mode="$(run_priv stat -c '%a' "$f" 2>/dev/null || true)"
  [[ "$mode" =~ ^(400|600)$ ]] || fail SECRET_MODE "${f} must be mode 0400/0600"
done

run_priv python3 - "$PROD_SECRETS_ENV_FILE" "$PROD_POSTGRES_PASSWORD_FILE" "${PROD_REQUIRE_FULL_SECRET_KEYS:-false}" <<'PY' \
  || fail SECRET_CONTENT_INVALID "production secrets failed safety or required-key validation"
import sys
from pathlib import Path

env_text = Path(sys.argv[1]).read_text(encoding="utf-8")
pw_text = Path(sys.argv[2]).read_text(encoding="utf-8").strip()
require_full = sys.argv[3].lower() == "true"
if not pw_text:
    raise SystemExit("empty postgres password")

kv = {}
for raw in env_text.splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    kv[k.strip()] = v.strip().strip('"').strip("'")

forbidden_prefixes = ("BROKER_API_KEY", "BROKER_SECRET", "EXCHANGE_PRIVATE_KEY", "LIVE_BROKER_TOKEN")
for k in kv:
    if any(k.upper().startswith(p) for p in forbidden_prefixes):
        raise SystemExit(f"forbidden broker credential {k} on control VM")

for unsafe in (
    "PANTHEON_LIVE_BROKER_ENABLED",
    "PANTHEON_PROVIDER_ENABLED",
    "PANTHEON_REAL_WRITES_ENABLED",
    "PANTHEON_DEV_STUB_WRITES_ENABLED",
    "PANTHEON_BFF_AUTH_STUB",
    "PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED",
    "PANTHEON_MGMT_NL_ASSISTANT_PROVIDER_ENABLED",
):
    if kv.get(unsafe, "").lower() in ("true", "1", "yes"):
        raise SystemExit(f"unsafe override {unsafe}={kv.get(unsafe)}")

if require_full:
    for req in ("PANTHEON_BFF_JWT_SECRET", "PANTHEON_RUNTIME_JWT_SECRET", "PANTHEON_DATABASE_URL"):
        if not kv.get(req):
            raise SystemExit(f"missing required secret {req}")
PY

[[ "$PROD_FE_HOST" != "$PROD_BFF_HOST" ]] || fail HOST_COLLISION "FE and BFF hosts must differ"
[[ "$PROD_IDP_ISSUER" == https://* ]] || fail IDP_NOT_HTTPS "IdP issuer must be https"

umask 077
run_priv mkdir -p \
  "${PROD_ROOT}"/{bin,manifests,state/journal,state/snapshots,fe/releases,fe/artifacts,proxy,config} \
  "${PROD_DATA_MOUNT}/postgres" "${PROD_DATA_MOUNT}/nats" \
  "$(dirname "$PROD_COMPOSE_ENV_FILE")" \
  || fail MKDIR "cannot create tree"

if [[ -f "${SCRIPT_DIR}/Caddyfile" ]]; then
  run_priv cp -f "${SCRIPT_DIR}/Caddyfile" "${PROD_ROOT}/proxy/Caddyfile" || fail INSTALL_CADDYFILE "cannot install Caddyfile"
fi
if [[ -f "${SCRIPT_DIR}/docker-compose.prod-control.yml" ]]; then
  run_priv cp -f "${SCRIPT_DIR}/docker-compose.prod-control.yml" "${PROD_ROOT}/docker-compose.prod-control.yml" || fail INSTALL_COMPOSE "cannot install compose file"
fi

# Placeholder upstream: explicit 503 until the first controller switch replaces it.
# Never clobbers an existing upstream when a release is already active.
if ! run_priv test -e "${PROD_ROOT}/proxy/bff_upstream.caddy"; then
  tmp_up="$(mktemp)"
  printf 'respond "no production release active" 503\n' > "$tmp_up"
  chmod 0644 "$tmp_up"
  run_priv mv -f "$tmp_up" "${PROD_ROOT}/proxy/bff_upstream.caddy" || fail UPSTREAM_INIT "cannot write placeholder upstream"
fi

# External same-origin runtime config, outside the sealed FE release dir (served no-store).
# Do not overwrite an active release's runtime-config.js if fe/current already exists.
if ! run_priv test -s "${PROD_ROOT}/config/runtime-config.js" || ! run_priv test -L "${PROD_ROOT}/fe/current"; then
  tmp_rc="$(mktemp)"
  python3 - "$PROD_BFF_HOST" "$PROD_IDP_ISSUER" > "$tmp_rc" <<'PY' || { rm -f "$tmp_rc"; fail RENDER "runtime-config render failed"; }
import json, sys
cfg = {
    "environment": "production",
    "release_id": None,
    "bffBaseUrl": f"https://{sys.argv[1]}",
    "oidcIssuer": sys.argv[2],
    "vite_bff_mode": "live",
    "vite_bff_fallback": "strict",
    "provider_enabled": False,
    "live_broker_enabled": False,
    "real_writes_enabled": False,
    "control_vm_broker_credentials_present": False,
}
body = (
    "window.__PANTHEON_DEPLOYMENT_CONFIG__ = Object.freeze(" + json.dumps(cfg, sort_keys=True) + ");\n"
    "window.__PANTHEON_RUNTIME_CONFIG__ = window.__PANTHEON_DEPLOYMENT_CONFIG__;\n"
)
sys.stdout.write(body)
PY
  chmod 0644 "$tmp_rc"
  run_priv mv -f "$tmp_rc" "${PROD_ROOT}/config/runtime-config.js" || fail RENDER "cannot install runtime-config.js"
fi

# Ensure compose.env exists for stateful / release compose invocations on a newly provisioned VM.
if ! run_priv test -f "$PROD_COMPOSE_ENV_FILE"; then
  tmp_ce="$(mktemp)"
  cat > "$tmp_ce" <<EOF
PROD_DATA_MOUNT=${PROD_DATA_MOUNT}
PROD_SECRETS_ENV_FILE=${PROD_SECRETS_ENV_FILE}
PROD_POSTGRES_PASSWORD_FILE=${PROD_POSTGRES_PASSWORD_FILE}
PROD_POSTGRES_USER=${PROD_POSTGRES_USER:-pantheon_prod}
PROD_POSTGRES_DB=${PROD_POSTGRES_DB:-pantheon_production}
PROD_JWT_ISSUER=${PROD_JWT_ISSUER:-${PROD_IDP_ISSUER}}
PROD_JWT_AUDIENCE=${PROD_JWT_AUDIENCE:-pantheon-production-api}
PROD_IDP_ISSUER=${PROD_IDP_ISSUER}
PROD_BFF_CORS_ORIGINS=https://${PROD_FE_HOST}
PROD_BLUE_PORT=${PROD_BLUE_PORT:-18001}
PROD_GREEN_PORT=${PROD_GREEN_PORT:-18002}
PANTHEON_POSTGRES_IMAGE=${PANTHEON_POSTGRES_IMAGE:-postgres:16-alpine@sha256:6b202572e5a92a4938fa080be515ad270515fc2d4a5850fa4ca10b0bd3efad89}
PANTHEON_NATS_IMAGE=${PANTHEON_NATS_IMAGE:-nats:2.10-alpine@sha256:d3eb053c882d39324bf4b1be5b4cb4dc1aa4bbbe0bf6ce3cdca90f2f2b0b12a7}
EOF
  chmod 0600 "$tmp_ce"
  run_priv mv -f "$tmp_ce" "$PROD_COMPOSE_ENV_FILE" || fail COMPOSE_ENV "cannot write compose.env"
fi

# Pre-acquire only immutable digest-pinned (@sha256:...) images (never build, never pull mutable tags).
if [[ "${PROD_PREPULL_IMAGES:-true}" == "true" ]]; then
  IMAGE_LIST="$(python3 - "${RELEASE_MANIFEST_PATH:-}" "${PANTHEON_POSTGRES_IMAGE:-}" "${PANTHEON_NATS_IMAGE:-}" <<'PY'
import json, re, sys
from pathlib import Path

DIGEST_RE = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
mpath_str, pg_img, nats_img = sys.argv[1:4]
images = []
for ref in (pg_img, nats_img):
    if ref:
        if not DIGEST_RE.match(ref):
            raise SystemExit(f"UNPINNED:{ref}")
        images.append(ref)

if mpath_str and Path(mpath_str).is_file():
    manifest = json.loads(Path(mpath_str).read_text(encoding="utf-8"))
    b_imgs = manifest.get("backend", {}).get("images", {})
    for key in ("operator_bff", "runtime_manager", "governance", "deployment", "lifecycle_projector"):
        val = str(b_imgs.get(key) or "").strip()
        if not DIGEST_RE.match(val):
            raise SystemExit(f"UNPINNED:{key}={val}")
        if val not in images:
            images.append(val)
    rb = manifest.get("rollback_baseline") or {}
    rb_bff = str(rb.get("backend_operator_bff_image") or "").strip()
    if rb_bff:
        if not DIGEST_RE.match(rb_bff):
            raise SystemExit(f"UNPINNED:rollback_operator_bff={rb_bff}")
        if rb_bff not in images:
            images.append(rb_bff)
    for k, val in (rb.get("backend_images") or {}).items():
        val = str(val or "").strip()
        if val:
            if not DIGEST_RE.match(val):
                raise SystemExit(f"UNPINNED:rollback_{k}={val}")
            if val not in images:
                images.append(val)

for img in images:
    print(img)
PY
  )" || fail IMAGE_NOT_DIGEST_PINNED "all production images must be immutable @sha256:... digests"

  if [[ "${PROD_REGISTRY_AUTH:-false}" == "true" ]]; then
    TOKEN_URL="${PROD_METADATA_TOKEN_URL:-http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token}"
    RAW_TOK="$("$CURL" -fsS -m 5 -H "Metadata-Flavor: Google" "$TOKEN_URL" 2>/dev/null)" \
      || fail REGISTRY_TOKEN_FAILED "cannot obtain runtime service account token from metadata server"
    ACCESS_TOK="$(python3 -c 'import json,sys;d=json.loads(sys.argv[1]);t=str(d.get("access_token") or "").strip();assert t;print(t)' "$RAW_TOK" 2>/dev/null)" \
      || fail REGISTRY_TOKEN_FAILED "invalid metadata token payload"
    REG_HOST="${PROD_REGISTRY_HOST:-asia-east1-docker.pkg.dev}"
    printf '%s' "$ACCESS_TOK" | run_priv "$DOCKER" login -u oauth2accesstoken --password-stdin "https://${REG_HOST}" >/dev/null 2>&1 \
      || fail REGISTRY_AUTH_FAILED "docker login to https://${REG_HOST} failed"
  fi

  while IFS= read -r img; do
    [[ -z "$img" ]] && continue
    run_priv "$DOCKER" pull "$img" >/dev/null 2>&1 || fail IMAGE_PULL_FAILED "failed to pull digest-pinned image ${img}"
    run_priv "$DOCKER" image inspect "$img" >/dev/null 2>&1 || fail IMAGE_INSPECT_FAILED "pulled image ${img} failed inspect"
  done <<< "$IMAGE_LIST"
fi

if [[ "${PROD_BOOTSTRAP_SKIP_STATEFUL:-false}" != "true" ]]; then
  run_priv "$DOCKER" compose -p pantheon-production -f "${SCRIPT_DIR}/docker-compose.prod-control.yml" \
    --env-file "$PROD_COMPOSE_ENV_FILE" --profile stateful up -d --no-build \
    || fail STATEFUL_START "stateful services failed to start"
fi
printf '{"status":"bootstrapped","first_release_requires":"PROD_BOOTSTRAP_FIRST_RELEASE=true","rollback_target":null}\n'
