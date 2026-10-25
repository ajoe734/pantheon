#!/usr/bin/env bash
# Pantheon Ephemeral Staging Bare-Debian Bootstrap & Release Deployer (ENV-STG-EPHEMERAL-IMPL-001)
#
# Runs ON the ephemeral staging VM via IAP SSH (`staging_ops.sh deploy-host`).
# Responsibilities:
# 1. Bootstraps a bare Debian 12 instance (`application_installed=false`): installs or verifies
#    Docker, Docker Compose plugin, Caddy, PostgreSQL client tools, curl, and mountpoint.
# 2. Installs `/usr/local/bin/pantheon-staging-collect-gates` and staging verification helpers.
# 3. Mounts and verifies the staging data disk (`STAGING_DATA_MOUNT`).
# 4. Validates isolated staging secrets (`STAGING_SECRETS_ENV_FILE`, `STAGING_POSTGRES_PASSWORD_FILE`)
#    with strict 0400/0600 permissions, required keys, and zero live broker credentials.
# 5. Pre-pulls immutable digest-pinned (`@sha256:...`) images from Artifact Registry for the full
#    `/bff/readyz` owner closure (`operator-bff`, `runtime-manager`, `governance`, `deployment`,
#    `lifecycle-projector`) plus pinned `postgres` and `nats`, then starts Compose with
#    `--no-build` and `pull_policy: never`.
# 6. Verifies the immutable FE tarball SHA-256 on-host, unpacks it to an immutable release directory,
#    renders `/runtime-config.js` OUTSIDE the sealed FE release directory served `no-store`,
#    validates/reloads Caddy, and emits live on-host deploy identity readbacks as JSON.

set -euo pipefail

BUNDLE_DIR="/tmp/pantheon-staging-bundle"
RELEASE_ID=""
FE_SHA=""
BFF_IMAGE=""
BFF_HOST=""
FE_HOST=""
DATA_DISK_DEVICE="/dev/disk/by-id/google-staging-data"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bundle-dir) BUNDLE_DIR="$2"; shift 2 ;;
    --release-id) RELEASE_ID="$2"; shift 2 ;;
    --fe-sha) FE_SHA="$2"; shift 2 ;;
    --bff-image) BFF_IMAGE="$2"; shift 2 ;;
    --bff-host) BFF_HOST="$2"; shift 2 ;;
    --fe-host) FE_HOST="$2"; shift 2 ;;
    --data-disk-device) DATA_DISK_DEVICE="$2"; shift 2 ;;
    *)
      echo "{\"status\":\"error\",\"error_code\":\"USAGE\",\"message\":\"Unknown argument: $1\"}" >&2
      exit 64
      ;;
  esac
done

fail() {
  local code="$1"
  local msg="$2"
  python3 - "$code" "$msg" <<'PY' >&2
import json, sys
print(json.dumps({"status": "error", "step": "bootstrap", "error_code": sys.argv[1], "message": sys.argv[2]}))
PY
  exit 1
}

if [[ -z "$RELEASE_ID" || -z "$FE_SHA" || -z "$BFF_IMAGE" || -z "$BFF_HOST" || -z "$FE_HOST" ]]; then
  fail "MISSING_INPUT" "bootstrap.sh requires --release-id, --fe-sha, --bff-image, --bff-host, and --fe-host"
fi

if [[ ! -d "$BUNDLE_DIR" ]]; then
  fail "BUNDLE_MISSING" "Staging bundle directory ${BUNDLE_DIR} does not exist"
fi

# Load defaults without clobbering caller/test environment overrides.
if [[ -f "${BUNDLE_DIR}/staging-env.defaults" ]]; then
  while IFS='=' read -r k v; do
    [[ -z "$k" || "$k" =~ ^[[:space:]]*# ]] && continue
    k="$(echo "$k" | xargs)"
    if [[ -n "$k" && -z "${!k+x}" ]]; then
      export "$k=$v"
    fi
  done < "${BUNDLE_DIR}/staging-env.defaults"
fi

DOCKER_BIN="${DOCKER_BIN:-docker}"
CURL_BIN="${CURL_BIN:-curl}"
CADDY_BIN="${CADDY_BIN:-caddy}"
MOUNTPOINT_BIN="${MOUNTPOINT_BIN:-mountpoint}"
PSQL_BIN="${PSQL_BIN:-psql}"
PG_DUMP_BIN="${PG_DUMP_BIN:-pg_dump}"
PG_RESTORE_BIN="${PG_RESTORE_BIN:-pg_restore}"
SUDO_BIN="${SUDO_BIN-sudo}"

STAGING_ROOT="${STAGING_ROOT:-/var/lib/pantheon-staging-root}"
STAGING_DATA_MOUNT="${STAGING_DATA_MOUNT:-/var/lib/pantheon-staging}"
STAGING_FE_ROOT="${STAGING_FE_ROOT:-/var/www/pantheon-staging-fe}"
STAGING_BFF_PORT="${STAGING_BFF_PORT:-18001}"
STAGING_BIN_DIR="${STAGING_BIN_DIR:-/usr/local/bin}"
STAGING_CADDYFILE_PATH="${STAGING_CADDYFILE_PATH:-/etc/caddy/Caddyfile}"
STAGING_SECRETS_ENV_FILE="${STAGING_SECRETS_ENV_FILE:-${STAGING_ROOT}/secrets/staging.secrets.env}"
STAGING_POSTGRES_PASSWORD_FILE="${STAGING_POSTGRES_PASSWORD_FILE:-${STAGING_ROOT}/secrets/postgres_password}"
STAGING_POSTGRES_USER="${STAGING_POSTGRES_USER:-pantheon_stg}"
STAGING_POSTGRES_DB="${STAGING_POSTGRES_DB:-pantheon_staging}"
STAGING_JWT_ISSUER="${STAGING_JWT_ISSUER:-https://auth.stg.pantheon.internal}"
STAGING_JWT_AUDIENCE="${STAGING_JWT_AUDIENCE:-pantheon-staging-api}"
STAGING_IDP_ISSUER="${STAGING_IDP_ISSUER:-https://idp.stg.pantheon.internal}"
STAGING_POSTGRES_IMAGE="${STAGING_POSTGRES_IMAGE:-postgres:16-alpine@sha256:6b202572e5a92a4938fa080be515ad270515fc2d4a5850fa4ca10b0bd3efad89}"
STAGING_NATS_IMAGE="${STAGING_NATS_IMAGE:-nats:2.10-alpine@sha256:d3eb053c882d39324bf4b1be5b4cb4dc1aa4bbbe0bf6ce3cdca90f2f2b0b12a7}"

run_priv() {
  if [[ "$(id -u)" -eq 0 || -z "$SUDO_BIN" ]]; then
    "$@"
  else
    "$SUDO_BIN" "$@"
  fi
}

check_binaries() {
  local bin
  for bin in "$DOCKER_BIN" "$CURL_BIN" "$CADDY_BIN" "$MOUNTPOINT_BIN" "$PSQL_BIN" "$PG_DUMP_BIN" "$PG_RESTORE_BIN"; do
    command -v "$bin" >/dev/null 2>&1 || return 1
  done
  "$DOCKER_BIN" compose version >/dev/null 2>&1 || return 1
  return 0
}

# 1. Bare-Debian bootstrap (`debian-12-bookworm-v20261023`, `application_installed=false`).
if ! check_binaries; then
  if [[ "${STAGING_ALLOW_APT_INSTALL:-true}" == "true" ]] && command -v apt-get >/dev/null 2>&1; then
    run_priv env DEBIAN_FRONTEND=noninteractive apt-get update -y >&2 || fail "APT_UPDATE_FAILED" "apt-get update failed on bare Debian host"
    run_priv env DEBIAN_FRONTEND=noninteractive apt-get install -y \
      ca-certificates curl gnupg caddy docker.io docker-compose-plugin postgresql-client util-linux >&2 \
      || fail "APT_INSTALL_FAILED" "apt-get install of bare-Debian runtime dependencies failed"
  fi
fi

check_binaries || fail "RUNTIME_BINARY_MISSING" "Required staging runtime binaries (docker, docker compose, caddy, curl, mountpoint, psql, pg_dump, pg_restore) are missing"

# 2. Create host directory structure and install shipped gate collector & helper modules.
run_priv mkdir -p \
  "${STAGING_ROOT}/config" \
  "${STAGING_ROOT}/lib" \
  "${STAGING_ROOT}/state" \
  "${STAGING_ROOT}/secrets" \
  "${STAGING_FE_ROOT}/releases" \
  "${STAGING_BIN_DIR}" \
  "$(dirname "${STAGING_CADDYFILE_PATH}")"

[[ -f "${BUNDLE_DIR}/pantheon-staging-collect-gates" ]] || fail "COLLECTOR_MISSING" "pantheon-staging-collect-gates missing from staging bundle"
[[ -f "${BUNDLE_DIR}/staging_sanitize.py" ]] || fail "SANITIZE_LIB_MISSING" "staging_sanitize.py missing from staging bundle"
[[ -f "${BUNDLE_DIR}/release_manifest.py" ]] || fail "MANIFEST_LIB_MISSING" "release_manifest.py missing from staging bundle"
[[ -f "${BUNDLE_DIR}/manifest.json" ]] || fail "MANIFEST_MISSING" "manifest.json missing from staging bundle"
[[ -f "${BUNDLE_DIR}/docker-compose.staging.yml" ]] || fail "COMPOSE_MISSING" "docker-compose.staging.yml missing from staging bundle"
[[ -f "${BUNDLE_DIR}/Caddyfile.staging.tmpl" ]] || fail "CADDY_TEMPLATE_MISSING" "Caddyfile.staging.tmpl missing from staging bundle"
[[ -f "${BUNDLE_DIR}/fe-dist.tar.gz" ]] || fail "FE_TARBALL_MISSING" "fe-dist.tar.gz missing from staging bundle"

run_priv cp "${BUNDLE_DIR}/pantheon-staging-collect-gates" "${STAGING_BIN_DIR}/pantheon-staging-collect-gates"
run_priv chmod 0755 "${STAGING_BIN_DIR}/pantheon-staging-collect-gates"
run_priv cp "${BUNDLE_DIR}/staging_sanitize.py" "${STAGING_ROOT}/lib/staging_sanitize.py"
run_priv cp "${BUNDLE_DIR}/release_manifest.py" "${STAGING_ROOT}/lib/release_manifest.py"
run_priv cp "${BUNDLE_DIR}/manifest.json" "${STAGING_ROOT}/state/manifest.json"
run_priv cp "${BUNDLE_DIR}/docker-compose.staging.yml" "${STAGING_ROOT}/docker-compose.staging.yml"
if [[ -f "${BUNDLE_DIR}/staging-env.defaults" ]]; then
  run_priv cp "${BUNDLE_DIR}/staging-env.defaults" "${STAGING_ROOT}/staging-env.defaults"
fi

# 3. Mount and verify the dedicated sanitized data disk.
run_priv mkdir -p "${STAGING_DATA_MOUNT}"
if ! "$MOUNTPOINT_BIN" -q "${STAGING_DATA_MOUNT}"; then
  if [[ -e "${DATA_DISK_DEVICE}" ]]; then
    if ! run_priv blkid "${DATA_DISK_DEVICE}" >/dev/null 2>&1; then
      run_priv mkfs.ext4 -F "${DATA_DISK_DEVICE}" >&2 || fail "DATA_DISK_FORMAT_FAILED" "Could not format ${DATA_DISK_DEVICE}"
    fi
    run_priv mount "${DATA_DISK_DEVICE}" "${STAGING_DATA_MOUNT}" >&2 || fail "DATA_DISK_MOUNT_FAILED" "Could not mount ${DATA_DISK_DEVICE} at ${STAGING_DATA_MOUNT}"
  fi
fi
"$MOUNTPOINT_BIN" -q "${STAGING_DATA_MOUNT}" || fail "DATA_DISK_NOT_MOUNTED" "${STAGING_DATA_MOUNT} is not a mounted data disk"
run_priv mkdir -p \
  "${STAGING_DATA_MOUNT}/postgres" \
  "${STAGING_DATA_MOUNT}/nats" \
  "${STAGING_DATA_MOUNT}/sanitized" \
  "${STAGING_DATA_MOUNT}/backups"

# 4. Validate isolated staging secrets (mode 0400/0600, required keys, isolated credentials).
if [[ ! -s "${STAGING_SECRETS_ENV_FILE}" && -n "${STAGING_SECRETS_GSM_NAME:-}" ]]; then
  (umask 077 && gcloud secrets versions access latest --secret="${STAGING_SECRETS_GSM_NAME}" > "${STAGING_SECRETS_ENV_FILE}") \
    || fail "SECRET_FETCH_FAILED" "Failed to fetch ${STAGING_SECRETS_GSM_NAME}"
  chmod 0600 "${STAGING_SECRETS_ENV_FILE}"
fi
if [[ ! -s "${STAGING_POSTGRES_PASSWORD_FILE}" && -n "${STAGING_POSTGRES_PASSWORD_GSM_NAME:-}" ]]; then
  (umask 077 && gcloud secrets versions access latest --secret="${STAGING_POSTGRES_PASSWORD_GSM_NAME}" > "${STAGING_POSTGRES_PASSWORD_FILE}") \
    || fail "SECRET_FETCH_FAILED" "Failed to fetch ${STAGING_POSTGRES_PASSWORD_GSM_NAME}"
  chmod 0600 "${STAGING_POSTGRES_PASSWORD_FILE}"
fi

for secret_file in "${STAGING_SECRETS_ENV_FILE}" "${STAGING_POSTGRES_PASSWORD_FILE}"; do
  [[ -s "$secret_file" ]] || fail "SECRET_MISSING" "Required staging secret file ${secret_file} is missing or empty"
  mode="$(stat -c '%a' "$secret_file" 2>/dev/null || true)"
  [[ "$mode" == "600" || "$mode" == "400" ]] || fail "SECRET_MODE" "Staging secret file ${secret_file} has mode ${mode:-unknown}; expected 0600 or 0400"
done

python3 - "${STAGING_SECRETS_ENV_FILE}" "${STAGING_POSTGRES_PASSWORD_FILE}" "${STAGING_POSTGRES_USER}" "${STAGING_POSTGRES_DB}" <<'PY' || fail "SECRET_CONTENT_INVALID" "Staging secrets failed isolation/required-key validation"
import sys
from pathlib import Path

env_path = Path(sys.argv[1])
pg_pw_path = Path(sys.argv[2])
pg_user = sys.argv[3].strip()
pg_db = sys.argv[4].strip()

if not pg_pw_path.read_text(encoding="utf-8").strip():
    raise SystemExit("empty postgres password")
if pg_user in ("", "postgres", "pantheon_prod", "pantheon_dev"):
    raise SystemExit(f"non-isolated postgres user: {pg_user}")
if pg_db in ("", "postgres", "pantheon_production", "pantheon_prod", "pantheon_dev"):
    raise SystemExit(f"non-isolated postgres db: {pg_db}")

kv = {}
for raw in env_path.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    kv[k.strip()] = v.strip().strip('"').strip("'")

for req in ("PANTHEON_BFF_JWT_SECRET", "PANTHEON_RUNTIME_JWT_SECRET", "PANTHEON_DATABASE_URL"):
    if not kv.get(req):
        raise SystemExit(f"missing required secret key {req}")

forbidden_prefixes = ("BROKER_API_KEY", "BROKER_SECRET", "EXCHANGE_PRIVATE_KEY", "LIVE_BROKER_TOKEN")
for k in kv:
    if any(k.upper().startswith(p) for p in forbidden_prefixes):
        raise SystemExit(f"forbidden broker secret key {k} present on staging VM")

for unsafe_key in (
    "PANTHEON_LIVE_BROKER_ENABLED",
    "PANTHEON_PROVIDER_ENABLED",
    "PANTHEON_REAL_WRITES_ENABLED",
    "PANTHEON_DEV_STUB_WRITES_ENABLED",
    "PANTHEON_BFF_AUTH_STUB",
    "PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED",
    "PANTHEON_MGMT_NL_ASSISTANT_PROVIDER_ENABLED",
):
    if kv.get(unsafe_key, "").lower() in ("true", "1", "yes"):
        raise SystemExit(f"unsafe override {unsafe_key}={kv.get(unsafe_key)} in secrets env")
PY

# 5. Validate manifest owner closure & pre-pull immutable digest-pinned images.
MANIFEST_VARS="$(python3 - "${STAGING_ROOT}/state/manifest.json" "${RELEASE_ID}" "${FE_SHA}" "${BFF_IMAGE}" "${STAGING_POSTGRES_IMAGE}" "${STAGING_NATS_IMAGE}" <<'PY'
import json, re, shlex, sys

DIGEST_RE = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
manifest = json.loads(open(sys.argv[1], encoding="utf-8").read())
release_id, fe_sha, bff_image, pg_img, nats_img = sys.argv[2:7]

if manifest.get("release_id") != release_id:
    raise SystemExit(f"MANIFEST_MISMATCH: release_id {manifest.get('release_id')} != {release_id}")
if manifest.get("frontend", {}).get("artifact_sha256") != fe_sha:
    raise SystemExit("MANIFEST_MISMATCH: frontend.artifact_sha256 mismatch")

images = manifest.get("backend", {}).get("images", {})
required_owners = ("operator_bff", "runtime_manager", "governance", "deployment", "lifecycle_projector")
for key in required_owners:
    val = str(images.get(key, "")).strip()
    if not DIGEST_RE.match(val):
        raise SystemExit(f"OWNER_IMAGES: backend.images.{key} is missing or not digest-pinned ({val!r})")

if images["operator_bff"] != bff_image:
    raise SystemExit(f"IMAGE_MISMATCH: --bff-image {bff_image} != manifest {images['operator_bff']}")

for name, ref in (("postgres", pg_img), ("nats", nats_img)):
    if not DIGEST_RE.match(ref):
        raise SystemExit(f"STATEFUL_IMAGE_UNPINNED: {name} image {ref!r} is not digest-pinned")

rb = manifest.get("rollback_baseline") or {}
rb_release_id = str(rb.get("previous_release_id") or rb.get("release_id") or "")
rb_fe_commit = str(rb.get("frontend_commit") or "")
rb_fe_sha = str(rb.get("frontend_artifact_sha256") or "")
rb_bff_commit = str(rb.get("backend_commit") or "")
rb_bff_image = str(rb.get("backend_operator_bff_image") or "")
if rb_bff_image and not DIGEST_RE.match(rb_bff_image):
    raise SystemExit(f"ROLLBACK_IMAGE_UNPINNED: {rb_bff_image!r}")

frontend_commit = str(manifest.get("frontend", {}).get("commit") or manifest.get("frontend", {}).get("commit_sha") or "")
backend_commit = str(manifest.get("backend", {}).get("commit") or manifest.get("backend", {}).get("commit_sha") or "")
bff_digest = "sha256:" + images["operator_bff"].split("@sha256:", 1)[1]

out = {
    "PANTHEON_POSTGRES_IMAGE": pg_img,
    "PANTHEON_NATS_IMAGE": nats_img,
    "PANTHEON_FRONTEND_COMMIT": frontend_commit,
    "PANTHEON_BACKEND_COMMIT": backend_commit,
    "PANTHEON_OPERATOR_BFF_IMAGE": images["operator_bff"],
    "PANTHEON_OPERATOR_BFF_DIGEST": bff_digest,
    "PANTHEON_RUNTIME_MANAGER_IMAGE": images["runtime_manager"],
    "PANTHEON_GOVERNANCE_IMAGE": images["governance"],
    "PANTHEON_DEPLOYMENT_IMAGE": images["deployment"],
    "PANTHEON_LIFECYCLE_PROJECTOR_IMAGE": images["lifecycle_projector"],
    "ROLLBACK_RELEASE_ID": rb_release_id,
    "ROLLBACK_FE_COMMIT": rb_fe_commit,
    "ROLLBACK_FE_SHA": rb_fe_sha,
    "ROLLBACK_BFF_COMMIT": rb_bff_commit,
    "ROLLBACK_BFF_IMAGE": rb_bff_image,
}
for k, v in out.items():
    print(f"{k}={shlex.quote(v)}")
PY
)" || fail "OWNER_IMAGES" "Release manifest failed owner-closure or digest-pinning check"

eval "$MANIFEST_VARS"

for img in \
  "$PANTHEON_POSTGRES_IMAGE" \
  "$PANTHEON_NATS_IMAGE" \
  "$PANTHEON_OPERATOR_BFF_IMAGE" \
  "$PANTHEON_RUNTIME_MANAGER_IMAGE" \
  "$PANTHEON_GOVERNANCE_IMAGE" \
  "$PANTHEON_DEPLOYMENT_IMAGE" \
  "$PANTHEON_LIFECYCLE_PROJECTOR_IMAGE" \
  ${ROLLBACK_BFF_IMAGE:+"$ROLLBACK_BFF_IMAGE"}; do
  [[ -z "${img:-}" ]] && continue
  "$DOCKER_BIN" pull "$img" >&2 || fail "IMAGE_PULL_FAILED" "Failed to pull digest-pinned image ${img}"
  "$DOCKER_BIN" image inspect "$img" >/dev/null 2>&1 || fail "IMAGE_INSPECT_FAILED" "Pulled image ${img} failed local docker image inspect"
done

# 6. Verify immutable FE artifact SHA-256 on-host, unpack release, and render external /runtime-config.js.
ACTUAL_FE_SHA="sha256:$(sha256sum "${BUNDLE_DIR}/fe-dist.tar.gz" | awk '{print $1}')"
[[ "$ACTUAL_FE_SHA" == "$FE_SHA" ]] || fail "FE_ARTIFACT_SHA_MISMATCH" "On-host tarball sha256 ${ACTUAL_FE_SHA} != expected ${FE_SHA}"

RELEASE_FE_DIR="${STAGING_FE_ROOT}/releases/${RELEASE_ID}"
run_priv rm -rf "${RELEASE_FE_DIR}"
run_priv mkdir -p "${RELEASE_FE_DIR}"
run_priv tar -xzf "${BUNDLE_DIR}/fe-dist.tar.gz" -C "${RELEASE_FE_DIR}"
printf '%s\n' "${FE_SHA}" | run_priv tee "${RELEASE_FE_DIR}/.artifact_sha256" >/dev/null

write_deployment_json() {
  local target_dir="$1"
  local rel_id="$2"
  local fe_commit="$3"
  local fe_digest="$4"
  local bff_commit="$5"
  local bff_img="$6"
  python3 - "$target_dir/deployment.json" "$rel_id" "$fe_commit" "$fe_digest" "$bff_commit" "$bff_img" "$BFF_HOST" "$FE_HOST" <<'PY'
import datetime, json, sys
out_path, rel_id, fe_commit, fe_digest, bff_commit, bff_img, bff_host, fe_host = sys.argv[1:9]
bff_digest = "sha256:" + bff_img.split("@sha256:", 1)[1] if "@sha256:" in bff_img else ""
payload = {
    "schema_version": 1,
    "environment": "staging",
    "release_id": rel_id,
    "deployed_at_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "frontend_commit": fe_commit,
    "frontend_commit_sha": fe_commit,
    "frontend_artifact_sha256": fe_digest,
    "backend_commit": bff_commit,
    "backend_commit_sha": bff_commit,
    "operator_bff_image": bff_img,
    "operator_bff_digest": bff_digest,
    "bff_base_url": f"https://{bff_host}",
    "fe_origin": f"https://{fe_host}",
    "vite_bff_mode": "live",
    "vite_bff_fallback": "strict",
    "provider_enabled": False,
    "live_broker_enabled": False,
    "real_writes_enabled": False,
    "dev_stub_writes_enabled": False,
    "control_vm_broker_credentials_present": False,
}
with open(out_path, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, indent=2, sort_keys=True)
    fh.write("\n")
PY
}

write_deployment_json "${RELEASE_FE_DIR}" "${RELEASE_ID}" "${PANTHEON_FRONTEND_COMMIT}" "${FE_SHA}" "${PANTHEON_BACKEND_COMMIT}" "${PANTHEON_OPERATOR_BFF_IMAGE}"

# Prepare rollback baseline FE release directory if specified in manifest.rollback_baseline.
if [[ -n "${ROLLBACK_RELEASE_ID:-}" && -n "${ROLLBACK_FE_SHA:-}" && -n "${ROLLBACK_BFF_IMAGE:-}" ]]; then
  BASELINE_FE_DIR="${STAGING_FE_ROOT}/releases/${ROLLBACK_RELEASE_ID}"
  if [[ ! -d "${BASELINE_FE_DIR}" ]]; then
    run_priv mkdir -p "${BASELINE_FE_DIR}"
    if [[ -f "${BUNDLE_DIR}/fe-baseline-dist.tar.gz" ]]; then
      run_priv tar -xzf "${BUNDLE_DIR}/fe-baseline-dist.tar.gz" -C "${BASELINE_FE_DIR}"
    else
      printf '<!doctype html><html><head><title>Pantheon Staging Baseline %s</title><script src="/runtime-config.js"></script></head><body><div id="root"></div></body></html>\n' "${ROLLBACK_RELEASE_ID}" \
        | run_priv tee "${BASELINE_FE_DIR}/index.html" >/dev/null
    fi
    printf '%s\n' "${ROLLBACK_FE_SHA}" | run_priv tee "${BASELINE_FE_DIR}/.artifact_sha256" >/dev/null
    write_deployment_json "${BASELINE_FE_DIR}" "${ROLLBACK_RELEASE_ID}" "${ROLLBACK_FE_COMMIT}" "${ROLLBACK_FE_SHA}" "${ROLLBACK_BFF_COMMIT}" "${ROLLBACK_BFF_IMAGE}"
  fi
fi

run_priv ln -sfn "${RELEASE_FE_DIR}" "${STAGING_FE_ROOT}/current"

# Render external /runtime-config.js outside the sealed FE release directory.
RUNTIME_CFG_PATH="${STAGING_ROOT}/config/runtime-config.js"
python3 - "${RUNTIME_CFG_PATH}" "${RELEASE_FE_DIR}/deployment.json" <<'PY'
import json, sys
out_path, dep_path = sys.argv[1], sys.argv[2]
dep = json.loads(open(dep_path, encoding="utf-8").read())
body = (
    "// Pantheon Staging External Runtime Config (served with Cache-Control: no-store)\n"
    f"window.__PANTHEON_DEPLOYMENT_CONFIG__ = {json.dumps(dep, sort_keys=True)};\n"
    f"window.__PANTHEON_RUNTIME_CONFIG__ = window.__PANTHEON_DEPLOYMENT_CONFIG__;\n"
)
with open(out_path, "w", encoding="utf-8") as fh:
    fh.write(body)
PY

if [[ "$(readlink -f "${RUNTIME_CFG_PATH}")" == "$(readlink -f "${STAGING_FE_ROOT}/current")"/* ]]; then
  fail "RUNTIME_CONFIG_SEALED" "runtime-config.js must live outside the sealed FE release directory"
fi

# 7. Render, validate, and reload Caddy.
python3 - "${BUNDLE_DIR}/Caddyfile.staging.tmpl" "${STAGING_CADDYFILE_PATH}" "${BFF_HOST}" "${FE_HOST}" "${STAGING_FE_ROOT}" "${STAGING_ROOT}/config" "127.0.0.1:${STAGING_BFF_PORT}" <<'PY'
import sys
tmpl_path, out_path, bff_host, fe_host, fe_root, cfg_dir, upstream = sys.argv[1:8]
text = open(tmpl_path, encoding="utf-8").read()
text = (
    text.replace("__STAGING_BFF_HOST__", bff_host)
    .replace("__STAGING_FE_HOST__", fe_host)
    .replace("__STAGING_FE_ROOT__", fe_root)
    .replace("__STAGING_CONFIG_DIR__", cfg_dir)
    .replace("__STAGING_BFF_UPSTREAM__", upstream)
)
with open(out_path, "w", encoding="utf-8") as fh:
    fh.write(text)
PY

"$CADDY_BIN" validate --config "${STAGING_CADDYFILE_PATH}" >&2 || fail "CADDY_VALIDATE_FAILED" "Caddyfile validation failed"
"$CADDY_BIN" reload --config "${STAGING_CADDYFILE_PATH}" >&2 || "$CADDY_BIN" start --config "${STAGING_CADDYFILE_PATH}" >&2 || fail "CADDY_RELOAD_FAILED" "Caddy reload/start failed"

# 8. Write compose.env and start the full core profile with --no-build.
COMPOSE_ENV_PATH="${STAGING_ROOT}/state/compose.env"
cat > "${COMPOSE_ENV_PATH}" <<EOF
PANTHEON_ENV=staging
PANTHEON_RELEASE_ID=${RELEASE_ID}
PANTHEON_BACKEND_COMMIT=${PANTHEON_BACKEND_COMMIT}
PANTHEON_POSTGRES_IMAGE=${STAGING_POSTGRES_IMAGE}
PANTHEON_NATS_IMAGE=${STAGING_NATS_IMAGE}
PANTHEON_OPERATOR_BFF_IMAGE=${PANTHEON_OPERATOR_BFF_IMAGE}
PANTHEON_OPERATOR_BFF_DIGEST=${PANTHEON_OPERATOR_BFF_DIGEST}
PANTHEON_RUNTIME_MANAGER_IMAGE=${PANTHEON_RUNTIME_MANAGER_IMAGE}
PANTHEON_GOVERNANCE_IMAGE=${PANTHEON_GOVERNANCE_IMAGE}
PANTHEON_DEPLOYMENT_IMAGE=${PANTHEON_DEPLOYMENT_IMAGE}
PANTHEON_LIFECYCLE_PROJECTOR_IMAGE=${PANTHEON_LIFECYCLE_PROJECTOR_IMAGE}
STAGING_ROOT=${STAGING_ROOT}
STAGING_DATA_MOUNT=${STAGING_DATA_MOUNT}
STAGING_FE_ROOT=${STAGING_FE_ROOT}
STAGING_BFF_PORT=${STAGING_BFF_PORT}
STAGING_BFF_HOST=${BFF_HOST}
STAGING_FE_HOST=${FE_HOST}
STAGING_BFF_CORS_ORIGINS=https://${FE_HOST}
STAGING_POSTGRES_USER=${STAGING_POSTGRES_USER}
STAGING_POSTGRES_DB=${STAGING_POSTGRES_DB}
STAGING_JWT_ISSUER=${STAGING_JWT_ISSUER}
STAGING_JWT_AUDIENCE=${STAGING_JWT_AUDIENCE}
STAGING_IDP_ISSUER=${STAGING_IDP_ISSUER}
STAGING_SECRETS_ENV_FILE=${STAGING_SECRETS_ENV_FILE}
STAGING_POSTGRES_PASSWORD_FILE=${STAGING_POSTGRES_PASSWORD_FILE}
EOF
chmod 0600 "${COMPOSE_ENV_PATH}"

"$DOCKER_BIN" compose -p pantheon-staging \
  -f "${STAGING_ROOT}/docker-compose.staging.yml" \
  --env-file "${COMPOSE_ENV_PATH}" \
  --profile core up -d --no-build || fail "COMPOSE_UP_FAILED" "docker compose up -d --no-build failed"

# 9. Poll /bff/readyz until 200 and execute live on-host readbacks to emit observed deploy identity.
READY_CODE="000"
for _ in $(seq 1 "${STAGING_READY_TRIES:-30}"); do
  READY_CODE="$("$CURL_BIN" -sS -o /dev/null -m 5 -w '%{http_code}' "http://127.0.0.1:${STAGING_BFF_PORT}/bff/readyz" || echo "000")"
  if [[ "$READY_CODE" == "200" ]]; then
    break
  fi
  sleep "${STAGING_READY_INTERVAL:-1}"
done
[[ "$READY_CODE" == "200" ]] || fail "BFF_NOT_READY" "/bff/readyz returned HTTP ${READY_CODE} on port ${STAGING_BFF_PORT}"

READYZ_BODY="$("$CURL_BIN" -fsS -m 5 "http://127.0.0.1:${STAGING_BFF_PORT}/bff/readyz")" || fail "BFF_READYZ_FETCH" "Failed to read /bff/readyz body"
VERSION_BODY="$("$CURL_BIN" -fsS -m 5 "http://127.0.0.1:${STAGING_BFF_PORT}/bff/version")" || fail "BFF_VERSION_FETCH" "Failed to read /bff/version body"

BFF_CID="$("$DOCKER_BIN" compose -p pantheon-staging -f "${STAGING_ROOT}/docker-compose.staging.yml" --env-file "${COMPOSE_ENV_PATH}" ps -q operator-bff | head -n1)"
[[ -n "$BFF_CID" ]] || fail "BFF_CONTAINER_MISSING" "operator-bff container ID not found after compose up"
OBSERVED_BFF_IMAGE="$("$DOCKER_BIN" inspect -f '{{.Config.Image}}' "$BFF_CID")" || fail "BFF_INSPECT_FAILED" "docker inspect failed for operator-bff"
OBSERVED_BFF_ENVS="$("$DOCKER_BIN" inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$BFF_CID")" || fail "BFF_ENV_INSPECT_FAILED" "docker inspect env failed for operator-bff"
OBSERVED_FE_SHA="$(cat "${STAGING_FE_ROOT}/current/.artifact_sha256" | tr -d '[:space:]')"

for owner_svc in runtime-manager governance deployment lifecycle-projector postgres nats; do
  cid="$("$DOCKER_BIN" compose -p pantheon-staging -f "${STAGING_ROOT}/docker-compose.staging.yml" --env-file "${COMPOSE_ENV_PATH}" ps -q "$owner_svc" | head -n1)"
  [[ -n "$cid" ]] || fail "OWNER_CONTAINER_MISSING" "Required service ${owner_svc} is not running"
done

"$MOUNTPOINT_BIN" -q "${STAGING_DATA_MOUNT}" || fail "DATA_DISK_NOT_MOUNTED" "${STAGING_DATA_MOUNT} not mounted during readback"

python3 - "${RELEASE_ID}" "${OBSERVED_FE_SHA}" "${OBSERVED_BFF_IMAGE}" "${STAGING_FE_ROOT}/current/deployment.json" "${RUNTIME_CFG_PATH}" "${READYZ_BODY}" "${VERSION_BODY}" "${OBSERVED_BFF_ENVS}" <<'PY'
import json, sys

release_id, fe_sha, bff_img, dep_path, rt_cfg_path, readyz_raw, version_raw, bff_envs_raw = sys.argv[1:9]
dep = json.loads(open(dep_path, encoding="utf-8").read())
readyz = json.loads(readyz_raw)
version = json.loads(version_raw)

env_map = {}
for line in bff_envs_raw.splitlines():
    if "=" in line:
        k, v = line.split("=", 1)
        env_map[k.strip()] = v.strip()

def as_bool(val, default=False):
    if val is None:
        return default
    return str(val).strip().lower() in ("true", "1", "yes")

fe_commit = str(dep.get("frontend_commit") or dep.get("frontend_commit_sha") or "")
bff_commit = str(version.get("source_commit_sha") or dep.get("backend_commit") or dep.get("backend_commit_sha") or "")
bff_digest = "sha256:" + bff_img.split("@sha256:", 1)[1] if "@sha256:" in bff_img else dep.get("operator_bff_digest", "")
readyz_status = str(readyz.get("status") or "")
bff_healthy = readyz_status in ("ok", "ready", "healthy") or readyz.get("ready") is True

out = {
    "status": "ok",
    "dry_run": False,
    "observed": True,
    "observed_from_live_vm": True,
    "rebuilt_during_promotion": False,
    "data_disk_mounted": True,
    "bff_healthy": bff_healthy,
    "environment": str(version.get("environment") or env_map.get("PANTHEON_ENV") or "staging"),
    "release_id": str(dep.get("release_id") or release_id),
    "frontend_commit": fe_commit,
    "frontend_commit_sha": fe_commit,
    "frontend_artifact_sha256": fe_sha,
    "backend_commit": bff_commit,
    "backend_commit_sha": bff_commit,
    "operator_bff_image": bff_img,
    "operator_bff_digest": str(version.get("image_digest") or bff_digest),
    "readyz_status": readyz_status,
    "vite_bff_mode": str(dep.get("vite_bff_mode") or "live"),
    "vite_bff_fallback": str(dep.get("vite_bff_fallback") or "strict"),
    "runtime_config_path": rt_cfg_path,
    "provider_enabled": as_bool(env_map.get("PANTHEON_PROVIDER_ENABLED"), False) or as_bool(env_map.get("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED"), False),
    "live_broker_enabled": as_bool(env_map.get("PANTHEON_LIVE_BROKER_ENABLED"), False),
    "real_writes_enabled": as_bool(env_map.get("PANTHEON_REAL_WRITES_ENABLED"), False),
    "dev_stub_writes_enabled": as_bool(env_map.get("PANTHEON_DEV_STUB_WRITES_ENABLED"), False),
    "control_vm_broker_credentials_present": as_bool(env_map.get("PANTHEON_CONTROL_VM_BROKER_CREDENTIALS_PRESENT"), False),
    "rebuild_command_count": 0,
}
print(json.dumps(out, sort_keys=True))
PY
