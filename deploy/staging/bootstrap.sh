#!/usr/bin/env bash
# Pantheon Ephemeral Staging Bare-Debian Bootstrap & Release Deployer (ENV-STG-EPHEMERAL-IMPL-001)
#
# Runs ON the ephemeral staging VM via IAP SSH (`staging_ops.sh deploy-host`) as a non-root
# OS Login user (`roles/compute.osAdminLogin`) elevating privileged operations via `run_priv`.
# Responsibilities:
# 1. Bootstraps a bare Debian 12 instance (`debian-12-bookworm-v20261023`, `application_installed=false`):
#    installs or verifies Docker, Docker Compose, Caddy, PostgreSQL client tools, curl, and mountpoint.
# 2. Installs `/usr/local/bin/pantheon-staging-collect-gates` and staging verification helpers via `run_priv`.
# 3. Mounts and verifies the staging data disk (`STAGING_DATA_MOUNT`).
# 4. Validates isolated staging secrets (`STAGING_SECRETS_ENV_FILE`, `STAGING_POSTGRES_PASSWORD_FILE`)
#    with strict 0400/0600 permissions, required keys, and zero live broker credentials.
# 5. Authenticates Docker to Artifact Registry (`asia-east1-docker.pkg.dev`) using the VM's attached
#    runtime service account (`pantheon-stg-runtime@pantheon-stg-20260902.iam.gserviceaccount.com`)
#    token from the GCE metadata server, pre-pulls immutable digest-pinned (`@sha256:...`) images for
#    both the candidate and rollback-baseline `/bff/readyz` owner closures (`operator-bff`,
#    `runtime-manager`, `governance`, `deployment`, `lifecycle-projector`) plus pinned `postgres`
#    and `nats`, and starts Compose with `--no-build` and `pull_policy: never`.
# 6. Verifies both the candidate (`fe-dist.tar.gz`) and rollback-baseline (`fe-baseline-dist.tar.gz`)
#    immutable FE tarball SHA-256 digests on-host (never fabricating a placeholder), unpacks them to
#    immutable release directories, renders `/runtime-config.js` OUTSIDE the sealed FE release
#    directory served `no-store`, validates/reloads Caddy, and emits live on-host deploy identity JSON.

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

STAGING_GCP_PROJECT_ID="${STAGING_GCP_PROJECT_ID:-pantheon-stg-20260902}"
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
STAGING_METADATA_TOKEN_URL="${STAGING_METADATA_TOKEN_URL:-http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token}"

LOCAL_WORK_DIR="$(mktemp -d "${BUNDLE_DIR}/work.XXXXXX")"
chmod 0700 "${LOCAL_WORK_DIR}"
trap 'rm -rf "${LOCAL_WORK_DIR}"' EXIT

run_priv() {
  if [[ ( "$(id -u)" -eq 0 && "${FAKE_OSLOGIN_NONROOT:-false}" != "true" ) || -z "$SUDO_BIN" ]]; then
    "$@"
  else
    "$SUDO_BIN" "$@"
  fi
}

run_compose() {
  run_priv "$DOCKER_BIN" compose "$@"
}

check_binaries() {
  local bin
  for bin in "$DOCKER_BIN" "$CURL_BIN" "$CADDY_BIN" "$MOUNTPOINT_BIN" "$PSQL_BIN" "$PG_DUMP_BIN" "$PG_RESTORE_BIN"; do
    command -v "$bin" >/dev/null 2>&1 || return 1
  done
  run_priv "$DOCKER_BIN" compose version >/dev/null 2>&1 || return 1
  return 0
}

fetch_runtime_access_token() {
  local token_json=""
  if token_json="$("$CURL_BIN" -fsS -m 5 -H "Metadata-Flavor: Google" "${STAGING_METADATA_TOKEN_URL}" 2>/dev/null)"; then
    if TOKEN_JSON="$token_json" python3 - <<'PY'
import json, os, sys
data = json.loads(os.environ["TOKEN_JSON"])
tok = str(data.get("access_token") or "").strip()
if not tok:
    raise SystemExit(1)
print(tok)
PY
    then
      return 0
    fi
  fi
  return 1
}

fetch_gsm_secret_to_file() {
  local secret_name="$1"
  local dest_path="$2"
  local tmp_sec="${LOCAL_WORK_DIR}/secret-$(basename "${dest_path}")"
  rm -f "${tmp_sec}"
  local access_token=""
  access_token="$(fetch_runtime_access_token)" || return 1
  local sm_url="https://secretmanager.googleapis.com/v1/projects/${STAGING_GCP_PROJECT_ID}/secrets/${secret_name}/versions/latest:access"
  local sm_resp=""
  sm_resp="$("$CURL_BIN" -fsS -m 10 -H "Authorization: Bearer ${access_token}" "${sm_url}" 2>/dev/null)" || return 1
  SM_RESP="${sm_resp}" TMP_SEC="${tmp_sec}" python3 - <<'PY' || return 1
import base64, json, os
from pathlib import Path
data = json.loads(os.environ["SM_RESP"])
b64 = data.get("payload", {}).get("data", "")
if not b64:
    raise SystemExit(1)
raw = base64.b64decode(b64)
p = Path(os.environ["TMP_SEC"])
p.write_bytes(raw)
p.chmod(0o600)
PY
  run_priv install -m 0600 "${tmp_sec}" "${dest_path}"
  rm -f "${tmp_sec}"
  return 0
}

# 1. Bare-Debian bootstrap (`debian-12-bookworm-v20261023`, `application_installed=false`).
if ! check_binaries; then
  if [[ "${STAGING_ALLOW_APT_INSTALL:-true}" == "true" ]] && command -v apt-get >/dev/null 2>&1; then
    run_priv env DEBIAN_FRONTEND=noninteractive apt-get update -y >&2 || fail "APT_UPDATE_FAILED" "apt-get update failed on bare Debian host"
    run_priv env DEBIAN_FRONTEND=noninteractive apt-get install -y \
      ca-certificates curl gnupg caddy postgresql-client util-linux python3 >&2 \
      || fail "APT_INSTALL_FAILED" "apt-get install of bare-Debian runtime dependencies failed"
    run_priv install -m 0755 -d /etc/apt/keyrings
    "$CURL_BIN" -fsSL https://download.docker.com/linux/debian/gpg | run_priv gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg \
      || fail "DOCKER_REPO_KEY_FAILED" "Failed to fetch the official Docker apt repository key"
    run_priv chmod a+r /etc/apt/keyrings/docker.gpg
    printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian bookworm stable\n' "$(dpkg --print-architecture)" \
      | run_priv tee /etc/apt/sources.list.d/docker.list >/dev/null
    run_priv env DEBIAN_FRONTEND=noninteractive apt-get update -y >&2 || fail "APT_UPDATE_FAILED" "apt-get update failed after adding the Docker repository"
    run_priv env DEBIAN_FRONTEND=noninteractive apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin >&2 \
      || fail "APT_DOCKER_INSTALL_FAILED" "Failed to install Docker Engine and docker-compose-plugin from the official Docker Debian 12 repository"
    if command -v systemctl >/dev/null 2>&1; then
      run_priv systemctl enable --now docker >/dev/null 2>&1 || true
    fi
  fi
fi

check_binaries || fail "RUNTIME_BINARY_MISSING" "Required staging runtime binaries (docker, docker compose, caddy, curl, mountpoint, psql, pg_dump, pg_restore) are missing"

# 2. Create host directory structure and install shipped gate collector & helper modules via run_priv.
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

run_priv install -m 0755 "${BUNDLE_DIR}/pantheon-staging-collect-gates" "${STAGING_BIN_DIR}/pantheon-staging-collect-gates"
run_priv install -m 0644 "${BUNDLE_DIR}/staging_sanitize.py" "${STAGING_ROOT}/lib/staging_sanitize.py"
run_priv install -m 0644 "${BUNDLE_DIR}/release_manifest.py" "${STAGING_ROOT}/lib/release_manifest.py"
run_priv install -m 0644 "${BUNDLE_DIR}/manifest.json" "${STAGING_ROOT}/state/manifest.json"
run_priv install -m 0644 "${BUNDLE_DIR}/docker-compose.staging.yml" "${STAGING_ROOT}/docker-compose.staging.yml"
if [[ -f "${BUNDLE_DIR}/staging-env.defaults" ]]; then
  run_priv install -m 0644 "${BUNDLE_DIR}/staging-env.defaults" "${STAGING_ROOT}/staging-env.defaults"
fi

# 3. Mount and verify the dedicated sanitized data disk via run_priv.
run_priv mkdir -p "${STAGING_DATA_MOUNT}"
if ! run_priv "$MOUNTPOINT_BIN" -q "${STAGING_DATA_MOUNT}"; then
  if [[ -e "${DATA_DISK_DEVICE}" ]]; then
    if ! run_priv blkid "${DATA_DISK_DEVICE}" >/dev/null 2>&1; then
      run_priv mkfs.ext4 -F "${DATA_DISK_DEVICE}" >&2 || fail "DATA_DISK_FORMAT_FAILED" "Could not format ${DATA_DISK_DEVICE}"
    fi
    run_priv mount "${DATA_DISK_DEVICE}" "${STAGING_DATA_MOUNT}" >&2 || fail "DATA_DISK_MOUNT_FAILED" "Could not mount ${DATA_DISK_DEVICE} at ${STAGING_DATA_MOUNT}"
  fi
fi
run_priv "$MOUNTPOINT_BIN" -q "${STAGING_DATA_MOUNT}" || fail "DATA_DISK_NOT_MOUNTED" "${STAGING_DATA_MOUNT} is not a mounted data disk"
run_priv mkdir -p \
  "${STAGING_DATA_MOUNT}/postgres" \
  "${STAGING_DATA_MOUNT}/nats" \
  "${STAGING_DATA_MOUNT}/sanitized" \
  "${STAGING_DATA_MOUNT}/backups"

# 4. Validate isolated staging secrets (mode 0400/0600, required keys, isolated credentials) via run_priv.
if ! run_priv test -s "${STAGING_SECRETS_ENV_FILE}" && [[ -n "${STAGING_SECRETS_GSM_NAME:-}" ]]; then
  fetch_gsm_secret_to_file "${STAGING_SECRETS_GSM_NAME}" "${STAGING_SECRETS_ENV_FILE}" \
    || fail "SECRET_FETCH_FAILED" "Failed to fetch ${STAGING_SECRETS_GSM_NAME}"
fi
if ! run_priv test -s "${STAGING_POSTGRES_PASSWORD_FILE}" && [[ -n "${STAGING_POSTGRES_PASSWORD_GSM_NAME:-}" ]]; then
  fetch_gsm_secret_to_file "${STAGING_POSTGRES_PASSWORD_GSM_NAME}" "${STAGING_POSTGRES_PASSWORD_FILE}" \
    || fail "SECRET_FETCH_FAILED" "Failed to fetch ${STAGING_POSTGRES_PASSWORD_GSM_NAME}"
fi

for secret_file in "${STAGING_SECRETS_ENV_FILE}" "${STAGING_POSTGRES_PASSWORD_FILE}"; do
  run_priv test -s "$secret_file" || fail "SECRET_MISSING" "Required staging secret file ${secret_file} is missing or empty"
  mode="$(run_priv stat -c '%a' "$secret_file" 2>/dev/null || true)"
  [[ "$mode" == "600" || "$mode" == "400" ]] || fail "SECRET_MODE" "Staging secret file ${secret_file} has mode ${mode:-unknown}; expected 0600 or 0400"
done

cat > "${LOCAL_WORK_DIR}/validate_secrets.py" <<'PY'
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

for req in (
    "PANTHEON_BFF_JWT_SECRET",
    "PANTHEON_RUNTIME_JWT_SECRET",
    "PANTHEON_DATABASE_URL",
    "TELEMETRY_DB_DSN",
    "LIFECYCLE_PROJECTOR_PROJECTION_DSN",
):
    if not kv.get(req):
        raise SystemExit(f"missing required secret key {req}")
for dsn_key in ("TELEMETRY_DB_DSN", "LIFECYCLE_PROJECTOR_PROJECTION_DSN"):
    dsn_base, _, dsn_query = kv[dsn_key].partition("?")
    dsn_path = dsn_base.rstrip("/")
    if dsn_query:
        from urllib.parse import parse_qs
        override_keys = {"dbname", "host", "hostaddr", "port", "user", "service", "options"}
        bad = override_keys & {k.lower() for k in parse_qs(dsn_query, keep_blank_values=True)}
        if bad:
            raise SystemExit(f"{dsn_key} must not carry routing override parameters: {sorted(bad)}")
    if not kv[dsn_key].startswith(("postgresql://", "postgres://")) or dsn_path.rsplit("/", 1)[-1] != pg_db:
        raise SystemExit(f"{dsn_key} must be a postgres DSN for isolated staging db {pg_db}")

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
run_priv python3 "${LOCAL_WORK_DIR}/validate_secrets.py" \
  "${STAGING_SECRETS_ENV_FILE}" \
  "${STAGING_POSTGRES_PASSWORD_FILE}" \
  "${STAGING_POSTGRES_USER}" \
  "${STAGING_POSTGRES_DB}" \
  || fail "SECRET_CONTENT_INVALID" "Staging secrets failed isolation/required-key validation"

# 5. Validate manifest owner closure & pre-pull immutable digest-pinned images (candidate + baseline).
MANIFEST_VARS="$(python3 - "${BUNDLE_DIR}/manifest.json" "${RELEASE_ID}" "${FE_SHA}" "${BFF_IMAGE}" "${STAGING_POSTGRES_IMAGE}" "${STAGING_NATS_IMAGE}" <<'PY'
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
rb_release_id = str(rb.get("previous_release_id") or rb.get("release_id") or "").strip()
rb_fe_commit = str(rb.get("frontend_commit") or "").strip()
rb_fe_sha = str(rb.get("frontend_artifact_sha256") or "").strip()
rb_bff_commit = str(rb.get("backend_commit") or "").strip()
rb_bff_image = str(rb.get("backend_operator_bff_image") or "").strip()
if not (rb_release_id and rb_fe_commit and rb_fe_sha and rb_bff_commit and rb_bff_image):
    raise SystemExit("ROLLBACK_BASELINE_INCOMPLETE: rollback_baseline is required for staging deployment")
if not DIGEST_RE.match(rb_bff_image):
    raise SystemExit(f"ROLLBACK_IMAGE_UNPINNED: {rb_bff_image!r}")

rb_images_map = dict(rb.get("backend_images") or {})
if not rb_images_map:
    raise SystemExit("ROLLBACK_BASELINE_INCOMPLETE: rollback_baseline.backend_images is required")
if str(rb_images_map.get("operator_bff") or "").strip() != rb_bff_image:
    raise SystemExit("ROLLBACK_BASELINE_INCOMPLETE: rollback_baseline.backend_images.operator_bff must equal backend_operator_bff_image")
for key in required_owners:
    rb_val = str(rb_images_map.get(key) or "").strip()
    if not rb_val:
        raise SystemExit(f"ROLLBACK_BASELINE_INCOMPLETE: rollback_baseline.backend_images.{key} is required")
    if not DIGEST_RE.match(rb_val):
        raise SystemExit(f"ROLLBACK_OWNER_IMAGE_UNPINNED: {key}={rb_val!r}")
    rb_images_map[key] = rb_val

frontend_commit = str(manifest.get("frontend", {}).get("commit") or manifest.get("frontend", {}).get("commit_sha") or "")
backend_commit = str(manifest.get("backend", {}).get("commit") or manifest.get("backend", {}).get("commit_sha") or "")
config_schema_version = str(manifest.get("config_schema_version") or "pantheon.env-config.v1")
rb_config_schema_version = str(rb.get("config_schema_version") or "").strip()
if not rb_config_schema_version:
    raise SystemExit("ROLLBACK_BASELINE_INCOMPLETE: rollback_baseline.config_schema_version is required")
bff_digest = "sha256:" + images["operator_bff"].split("@sha256:", 1)[1]
rb_bff_digest = "sha256:" + rb_bff_image.split("@sha256:", 1)[1]
reg_host = images["operator_bff"].split("/", 1)[0] if "/" in images["operator_bff"] else ""

out = {
    "PANTHEON_POSTGRES_IMAGE": pg_img,
    "PANTHEON_NATS_IMAGE": nats_img,
    "PANTHEON_FRONTEND_COMMIT": frontend_commit,
    "PANTHEON_BACKEND_COMMIT": backend_commit,
    "PANTHEON_CONFIG_SCHEMA_VERSION": config_schema_version,
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
    "ROLLBACK_CONFIG_SCHEMA_VERSION": rb_config_schema_version,
    "ROLLBACK_BFF_IMAGE": rb_images_map["operator_bff"],
    "ROLLBACK_BFF_DIGEST": rb_bff_digest,
    "ROLLBACK_RUNTIME_MANAGER_IMAGE": rb_images_map["runtime_manager"],
    "ROLLBACK_GOVERNANCE_IMAGE": rb_images_map["governance"],
    "ROLLBACK_DEPLOYMENT_IMAGE": rb_images_map["deployment"],
    "ROLLBACK_LIFECYCLE_PROJECTOR_IMAGE": rb_images_map["lifecycle_projector"],
    "STAGING_REGISTRY_HOST": reg_host,
}
for k, v in out.items():
    print(f"{k}={shlex.quote(v)}")
PY
)" || fail "OWNER_IMAGES" "Release manifest failed owner-closure or digest-pinning check"

eval "$MANIFEST_VARS"

# Authenticate Docker to Artifact Registry using the VM runtime service account token before pulling images.
if [[ -n "${STAGING_REGISTRY_HOST:-}" && "${STAGING_REGISTRY_HOST}" == *"."* ]]; then
  AR_ACCESS_TOKEN="$(fetch_runtime_access_token)" \
    || fail "REGISTRY_TOKEN_FAILED" "Failed to obtain runtime service account access token from metadata server for ${STAGING_REGISTRY_HOST}"
  printf '%s' "${AR_ACCESS_TOKEN}" | run_priv "$DOCKER_BIN" login -u oauth2accesstoken --password-stdin "https://${STAGING_REGISTRY_HOST}" >&2 \
    || fail "REGISTRY_AUTH_FAILED" "Docker login to https://${STAGING_REGISTRY_HOST} failed under runtime service account"
fi

for img in \
  "$PANTHEON_POSTGRES_IMAGE" \
  "$PANTHEON_NATS_IMAGE" \
  "$PANTHEON_OPERATOR_BFF_IMAGE" \
  "$PANTHEON_RUNTIME_MANAGER_IMAGE" \
  "$PANTHEON_GOVERNANCE_IMAGE" \
  "$PANTHEON_DEPLOYMENT_IMAGE" \
  "$PANTHEON_LIFECYCLE_PROJECTOR_IMAGE" \
  "$ROLLBACK_BFF_IMAGE" \
  "$ROLLBACK_RUNTIME_MANAGER_IMAGE" \
  "$ROLLBACK_GOVERNANCE_IMAGE" \
  "$ROLLBACK_DEPLOYMENT_IMAGE" \
  "$ROLLBACK_LIFECYCLE_PROJECTOR_IMAGE"; do
  [[ -z "${img:-}" ]] && continue
  run_priv "$DOCKER_BIN" pull "$img" >&2 || fail "IMAGE_PULL_FAILED" "Failed to pull digest-pinned image ${img}"
  run_priv "$DOCKER_BIN" image inspect "$img" >/dev/null 2>&1 || fail "IMAGE_INSPECT_FAILED" "Pulled image ${img} failed local docker image inspect"
done

# 6. Verify immutable candidate and baseline FE artifact SHA-256 on-host (never fabricate baseline!).
ACTUAL_FE_SHA="sha256:$(sha256sum "${BUNDLE_DIR}/fe-dist.tar.gz" | awk '{print $1}')"
[[ "$ACTUAL_FE_SHA" == "$FE_SHA" ]] || fail "FE_ARTIFACT_SHA_MISMATCH" "On-host candidate tarball sha256 ${ACTUAL_FE_SHA} != expected ${FE_SHA}"

[[ -f "${BUNDLE_DIR}/fe-baseline-dist.tar.gz" ]] || fail "BASELINE_FE_TARBALL_MISSING" "fe-baseline-dist.tar.gz missing from staging bundle; refusing to fabricate baseline FE release"
ACTUAL_BASELINE_FE_SHA="sha256:$(sha256sum "${BUNDLE_DIR}/fe-baseline-dist.tar.gz" | awk '{print $1}')"
[[ "$ACTUAL_BASELINE_FE_SHA" == "$ROLLBACK_FE_SHA" ]] || fail "BASELINE_FE_ARTIFACT_SHA_MISMATCH" "On-host baseline tarball sha256 ${ACTUAL_BASELINE_FE_SHA} != expected ${ROLLBACK_FE_SHA}"

run_priv install -m 0644 "${BUNDLE_DIR}/fe-dist.tar.gz" "${STAGING_ROOT}/state/fe-candidate-dist.tar.gz"
run_priv install -m 0644 "${BUNDLE_DIR}/fe-baseline-dist.tar.gz" "${STAGING_ROOT}/state/fe-baseline-dist.tar.gz"

write_deployment_json() {
  local target_dir="$1"
  local rel_id="$2"
  local fe_commit="$3"
  local fe_digest="$4"
  local bff_commit="$5"
  local bff_img="$6"
  local cfg_ver="$7"
  local tmp_dep="${LOCAL_WORK_DIR}/deployment-${rel_id}.json"
  python3 - "$tmp_dep" "$rel_id" "$fe_commit" "$fe_digest" "$bff_commit" "$bff_img" "$BFF_HOST" "$FE_HOST" "$cfg_ver" <<'PY'
import datetime, json, sys
out_path, rel_id, fe_commit, fe_digest, bff_commit, bff_img, bff_host, fe_host, cfg_ver = sys.argv[1:10]
bff_digest = "sha256:" + bff_img.split("@sha256:", 1)[1] if "@sha256:" in bff_img else ""
payload = {
    "schema_version": 1,
    "environment": "staging",
    "release_id": rel_id,
    "config_schema_version": cfg_ver,
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
  run_priv install -m 0644 "$tmp_dep" "$target_dir/deployment.json"
}

render_runtime_config() {
  local dep_json_path="$1"
  local dest_cfg_path="$2"
  local tmp_rt="${LOCAL_WORK_DIR}/$(basename "${dest_cfg_path}")"
  # Emits ONLY the accepted FE `pantheon.fe.public-runtime-config.v1` fields. Deployment identity
  # metadata stays in deployment.json; public Identity values come from direct environment wiring.
  python3 - "$tmp_rt" "$dep_json_path" <<'PY'
import hashlib, json, os, re, sys
out_path, dep_path = sys.argv[1], sys.argv[2]
dep = json.loads(open(dep_path, encoding="utf-8").read())

def need(name):
    val = os.environ.get(name, "").strip()
    if not val:
        print(json.dumps({"status": "error", "step": "bootstrap", "error_code": "PUBLIC_IDENTITY_CONFIG_MISSING",
                          "message": f"{name} is required (public isolated Identity config; no fallback)"}), file=sys.stderr)
        sys.exit(1)
    return val

api_key = need("STAGING_PUBLIC_GCP_IDENTITY_API_KEY")
project_id = need("STAGING_PUBLIC_GCP_IDENTITY_PROJECT_ID")
auth_domain = need("STAGING_PUBLIC_GCP_IDENTITY_AUTH_DOMAIN")
issuer = os.environ.get("STAGING_PUBLIC_GCP_IDENTITY_ISSUER", "").strip() or f"https://securetoken.google.com/{project_id}"
audience = os.environ.get("STAGING_PUBLIC_GCP_IDENTITY_AUDIENCE", "").strip() or project_id
cfg = {
    "schemaVersion": "pantheon.fe.public-runtime-config.v1",
    "environment": "staging",
    "configRef": str(dep["release_id"]),
    "bffBaseUrl": str(dep["bff_base_url"]),
    "gcpIdentity": {
        "apiKey": api_key,
        "projectId": project_id,
        "authDomain": auth_domain,
        "issuer": issuer,
        "audience": audience,
    },
}
canonical = json.dumps(cfg, sort_keys=True, separators=(",", ":"))
cfg["configSha256"] = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
body = (
    "// Pantheon Staging External Runtime Config (served with Cache-Control: no-store)\n"
    f"window.__PANTHEON_DEPLOYMENT_CONFIG__ = {json.dumps(cfg, sort_keys=True)};\n"
)
with open(out_path, "w", encoding="utf-8") as fh:
    fh.write(body)
PY
  run_priv install -m 0644 "$tmp_rt" "$dest_cfg_path"
}

RELEASE_FE_DIR="${STAGING_FE_ROOT}/releases/${RELEASE_ID}"
run_priv rm -rf "${RELEASE_FE_DIR}"
run_priv mkdir -p "${RELEASE_FE_DIR}"
run_priv tar -xzf "${BUNDLE_DIR}/fe-dist.tar.gz" -C "${RELEASE_FE_DIR}"
printf '%s\n' "${FE_SHA}" > "${LOCAL_WORK_DIR}/candidate.artifact_sha256"
run_priv install -m 0644 "${LOCAL_WORK_DIR}/candidate.artifact_sha256" "${RELEASE_FE_DIR}/.artifact_sha256"
write_deployment_json "${RELEASE_FE_DIR}" "${RELEASE_ID}" "${PANTHEON_FRONTEND_COMMIT}" "${FE_SHA}" "${PANTHEON_BACKEND_COMMIT}" "${PANTHEON_OPERATOR_BFF_IMAGE}" "${PANTHEON_CONFIG_SCHEMA_VERSION}"

BASELINE_FE_DIR="${STAGING_FE_ROOT}/releases/${ROLLBACK_RELEASE_ID}"
run_priv rm -rf "${BASELINE_FE_DIR}"
run_priv mkdir -p "${BASELINE_FE_DIR}"
run_priv tar -xzf "${BUNDLE_DIR}/fe-baseline-dist.tar.gz" -C "${BASELINE_FE_DIR}"
printf '%s\n' "${ROLLBACK_FE_SHA}" > "${LOCAL_WORK_DIR}/baseline.artifact_sha256"
run_priv install -m 0644 "${LOCAL_WORK_DIR}/baseline.artifact_sha256" "${BASELINE_FE_DIR}/.artifact_sha256"
write_deployment_json "${BASELINE_FE_DIR}" "${ROLLBACK_RELEASE_ID}" "${ROLLBACK_FE_COMMIT}" "${ROLLBACK_FE_SHA}" "${ROLLBACK_BFF_COMMIT}" "${ROLLBACK_BFF_IMAGE}" "${ROLLBACK_CONFIG_SCHEMA_VERSION}"

run_priv ln -sfn "${RELEASE_FE_DIR}" "${STAGING_FE_ROOT}/current"

# Render external /runtime-config.js (and candidate/baseline templates) outside the sealed FE release directory.
RUNTIME_CFG_PATH="${STAGING_ROOT}/config/runtime-config.js"
render_runtime_config "${RELEASE_FE_DIR}/deployment.json" "${STAGING_ROOT}/config/runtime-config.candidate.js"
render_runtime_config "${BASELINE_FE_DIR}/deployment.json" "${STAGING_ROOT}/config/runtime-config.baseline.js"
render_runtime_config "${RELEASE_FE_DIR}/deployment.json" "${RUNTIME_CFG_PATH}"

if [[ "$(readlink -f "${RUNTIME_CFG_PATH}")" == "$(readlink -f "${STAGING_FE_ROOT}/current")"/* ]]; then
  fail "RUNTIME_CONFIG_SEALED" "runtime-config.js must live outside the sealed FE release directory"
fi

# 7. Render, validate, and reload Caddy via run_priv.
python3 - "${BUNDLE_DIR}/Caddyfile.staging.tmpl" "${LOCAL_WORK_DIR}/Caddyfile" "${BFF_HOST}" "${FE_HOST}" "${STAGING_FE_ROOT}" "${STAGING_ROOT}/config" "127.0.0.1:${STAGING_BFF_PORT}" <<'PY'
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
run_priv install -m 0644 "${LOCAL_WORK_DIR}/Caddyfile" "${STAGING_CADDYFILE_PATH}"

run_priv "$CADDY_BIN" validate --config "${STAGING_CADDYFILE_PATH}" >&2 || fail "CADDY_VALIDATE_FAILED" "Caddyfile validation failed"
run_priv "$CADDY_BIN" reload --config "${STAGING_CADDYFILE_PATH}" >&2 || run_priv "$CADDY_BIN" start --config "${STAGING_CADDYFILE_PATH}" >&2 || fail "CADDY_RELOAD_FAILED" "Caddy reload/start failed"

# 8. Write compose.env and compose.baseline.env (full owner closure) and start core profile with --no-build.
COMPOSE_ENV_PATH="${STAGING_ROOT}/state/compose.env"
BASELINE_COMPOSE_ENV_PATH="${STAGING_ROOT}/state/compose.baseline.env"

cat > "${LOCAL_WORK_DIR}/compose.env" <<EOF
PANTHEON_ENV=staging
PANTHEON_RELEASE_ID=${RELEASE_ID}
PANTHEON_BACKEND_COMMIT=${PANTHEON_BACKEND_COMMIT}
PANTHEON_CONFIG_SCHEMA_VERSION=${PANTHEON_CONFIG_SCHEMA_VERSION}
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
run_priv install -m 0600 "${LOCAL_WORK_DIR}/compose.env" "${COMPOSE_ENV_PATH}"

cat > "${LOCAL_WORK_DIR}/compose.baseline.env" <<EOF
PANTHEON_ENV=staging
PANTHEON_RELEASE_ID=${ROLLBACK_RELEASE_ID}
PANTHEON_BACKEND_COMMIT=${ROLLBACK_BFF_COMMIT}
PANTHEON_CONFIG_SCHEMA_VERSION=${ROLLBACK_CONFIG_SCHEMA_VERSION}
PANTHEON_POSTGRES_IMAGE=${STAGING_POSTGRES_IMAGE}
PANTHEON_NATS_IMAGE=${STAGING_NATS_IMAGE}
PANTHEON_OPERATOR_BFF_IMAGE=${ROLLBACK_BFF_IMAGE}
PANTHEON_OPERATOR_BFF_DIGEST=${ROLLBACK_BFF_DIGEST}
PANTHEON_RUNTIME_MANAGER_IMAGE=${ROLLBACK_RUNTIME_MANAGER_IMAGE}
PANTHEON_GOVERNANCE_IMAGE=${ROLLBACK_GOVERNANCE_IMAGE}
PANTHEON_DEPLOYMENT_IMAGE=${ROLLBACK_DEPLOYMENT_IMAGE}
PANTHEON_LIFECYCLE_PROJECTOR_IMAGE=${ROLLBACK_LIFECYCLE_PROJECTOR_IMAGE}
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
run_priv install -m 0600 "${LOCAL_WORK_DIR}/compose.baseline.env" "${BASELINE_COMPOSE_ENV_PATH}"

run_compose -p pantheon-staging \
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

BFF_CID="$(run_compose -p pantheon-staging -f "${STAGING_ROOT}/docker-compose.staging.yml" --env-file "${COMPOSE_ENV_PATH}" ps -q operator-bff | head -n1)"
[[ -n "$BFF_CID" ]] || fail "BFF_CONTAINER_MISSING" "operator-bff container ID not found after compose up"
OBSERVED_BFF_IMAGE="$(run_priv "$DOCKER_BIN" inspect -f '{{.Config.Image}}' "$BFF_CID")" || fail "BFF_INSPECT_FAILED" "docker inspect failed for operator-bff"
OBSERVED_BFF_ENVS="$(run_priv "$DOCKER_BIN" inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$BFF_CID")" || fail "BFF_ENV_INSPECT_FAILED" "docker inspect env failed for operator-bff"
OBSERVED_FE_SHA="$(cat "${STAGING_FE_ROOT}/current/.artifact_sha256" | tr -d '[:space:]')"

for owner_svc in runtime-manager governance deployment lifecycle-projector postgres nats; do
  cid="$(run_compose -p pantheon-staging -f "${STAGING_ROOT}/docker-compose.staging.yml" --env-file "${COMPOSE_ENV_PATH}" ps -q "$owner_svc" | head -n1)"
  [[ -n "$cid" ]] || fail "OWNER_CONTAINER_MISSING" "Required service ${owner_svc} is not running"
done

run_priv "$MOUNTPOINT_BIN" -q "${STAGING_DATA_MOUNT}" || fail "DATA_DISK_NOT_MOUNTED" "${STAGING_DATA_MOUNT} not mounted during readback"

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
