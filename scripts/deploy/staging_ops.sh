#!/usr/bin/env bash
# Pantheon Ephemeral Staging Host & Immutable Artifact Adapter (`ENV-STG-EPHEMERAL-IMPL-001`)
#
# Subcommands:
# - `verify-artifacts`: Validates the immutable release manifest (`dev_verified` required),
#   retrieves/hashes the FE artifact bundle (`STAGING_FE_ARTIFACT_PATH` or `STAGING_FE_ARTIFACT_URI`),
#   queries the BFF container digest (`@sha256:...`) via `GCLOUD_BIN` in live mode, and fails closed
#   on any missing artifact or checksum/digest mismatch without rebuilding FE or BFF.
# - `deploy-host`: Creates `/tmp/pantheon-staging-bundle` on the target VM over IAP SSH
#   (`--tunnel-through-iap`), transfers the immutable FE tarball, release manifest, compose overlay,
#   Caddy template, `bootstrap.sh`, and `pantheon-staging-collect-gates` via IAP SCP
#   (`--tunnel-through-iap`), executes `bootstrap.sh` over IAP SSH to bootstrap bare Debian,
#   validate isolated secrets, pull digest-pinned owner-closure images, unpack the FE bundle,
#   render external `/runtime-config.js`, start Compose `--no-build`, and query live on-host
#   deploy identity (`docker inspect`, `curl /bff/readyz`, `curl /bff/version`, `deployment.json`,
#   `.artifact_sha256`, `mountpoint`).
# - `collect-verify-gates`: Invokes `/usr/local/bin/pantheon-staging-collect-gates` on the live
#   staging VM over IAP SSH (`--tunnel-through-iap`) to execute live migration, backup/restore,
#   readability, exact-pair rollback, and security/safe-off checks.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DEFAULTS_FILE="${REPO_ROOT}/deploy/staging/staging-env.defaults"

# Load defaults without overwriting caller/test environment overrides.
if [[ -f "${DEFAULTS_FILE}" ]]; then
  while IFS='=' read -r k v; do
    [[ -z "$k" || "$k" =~ ^[[:space:]]*# ]] && continue
    k="$(echo "$k" | xargs)"
    if [[ -n "$k" && -z "${!k+x}" ]]; then
      export "$k=$v"
    fi
  done < "${DEFAULTS_FILE}"
fi

die_json() {
  local code="$1"
  local msg="$2"
  printf '{"status":"error","error_code":"%s","message":"%s"}\n' "$code" "$msg" >&2
  exit 1
}

SUBCOMMAND="${1:-}"
if [[ -z "${SUBCOMMAND}" ]]; then
  die_json "MISSING_SUBCOMMAND" "Usage: staging_ops.sh <verify-artifacts|deploy-host|collect-verify-gates>"
fi
shift

DRY_RUN="${STAGING_DRY_RUN:-false}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN="true"
      shift
      ;;
    *)
      die_json "UNKNOWN_ARGUMENT" "Unknown argument to staging_ops.sh: $1"
      ;;
  esac
done

require_live_gcloud() {
  if [[ -z "${GCLOUD_BIN:-}" ]]; then
    die_json "GCLOUD_BIN_REQUIRED" "Live staging operation (STAGING_DRY_RUN=false) requires GCLOUD_BIN to be set"
  fi
  if ! command -v "${GCLOUD_BIN}" >/dev/null 2>&1; then
    die_json "GCLOUD_BIN_NOT_EXECUTABLE" "Configured GCLOUD_BIN (${GCLOUD_BIN}) is not executable"
  fi
}

if [[ -z "${RELEASE_MANIFEST_PATH:-}" || ! -f "${RELEASE_MANIFEST_PATH}" ]]; then
  die_json "MISSING_MANIFEST" "RELEASE_MANIFEST_PATH is required and must exist"
fi
if [[ -z "${EXPECTED_MANIFEST_SHA256:-}" ]]; then
  die_json "MISSING_EXPECTED_SHA256" "EXPECTED_MANIFEST_SHA256 is required"
fi

STATE_DIR="${STAGING_STATE_DIR:-/tmp/pantheon-staging-state}"
mkdir -p "${STATE_DIR}/evidence" "${STATE_DIR}/artifacts" "${STATE_DIR}/observed-gates"

validate_and_inspect_artifacts() {
  local base_summary
  if ! base_summary="$(
    python3 "${SCRIPT_DIR}/release_manifest.py" validate \
      --manifest "${RELEASE_MANIFEST_PATH}" \
      --expected-sha256 "${EXPECTED_MANIFEST_SHA256}" \
      --require-dev-verified 2>&1
  )"; then
    die_json "MANIFEST_VALIDATION_FAILED" "Release manifest rejected: $(printf '%s' "$base_summary" | tr '\n"' '  ')"
  fi

  local release_id expected_fe_sha expected_bff_image prev_release_id expected_base_fe_sha expected_base_bff_image
  release_id="$(MANIFEST_JSON="$base_summary" python3 -c 'import json, os; print(json.loads(os.environ["MANIFEST_JSON"])["release_id"])')"
  expected_fe_sha="$(MANIFEST_JSON="$base_summary" python3 -c 'import json, os; print(json.loads(os.environ["MANIFEST_JSON"])["frontend_artifact_sha256"])')"
  expected_bff_image="$(MANIFEST_JSON="$base_summary" python3 -c 'import json, os; print(json.loads(os.environ["MANIFEST_JSON"])["operator_bff_image"])')"
  prev_release_id="$(MANIFEST_JSON="$base_summary" python3 -c 'import json, os; print(json.loads(os.environ["MANIFEST_JSON"]).get("rollback_previous_release_id", ""))')"
  expected_base_fe_sha="$(MANIFEST_JSON="$base_summary" python3 -c 'import json, os; print(json.loads(os.environ["MANIFEST_JSON"]).get("rollback_frontend_artifact_sha256", ""))')"
  expected_base_bff_image="$(MANIFEST_JSON="$base_summary" python3 -c 'import json, os; print(json.loads(os.environ["MANIFEST_JSON"]).get("rollback_operator_bff_image", ""))')"

  local observed_fe_sha="${OBSERVED_FE_ARTIFACT_SHA256:-}"
  local fe_artifact_file="${STAGING_FE_ARTIFACT_PATH:-}"
  local observed_base_fe_sha="${OBSERVED_BASELINE_FE_ARTIFACT_SHA256:-}"
  local fe_baseline_artifact_file="${STAGING_FE_BASELINE_ARTIFACT_PATH:-}"

  if [[ "${DRY_RUN}" != "true" ]]; then
    require_live_gcloud
    if [[ -z "${fe_artifact_file}" && -n "${STAGING_FE_ARTIFACT_URI:-}" ]]; then
      fe_artifact_file="${STATE_DIR}/artifacts/${release_id}-fe-bundle.tar.gz"
      if ! "${GCLOUD_BIN}" storage cp "${STAGING_FE_ARTIFACT_URI}" "${fe_artifact_file}" >/dev/null 2>&1; then
        die_json "FE_ARTIFACT_RETRIEVAL_FAILED" "Failed to retrieve FE artifact from ${STAGING_FE_ARTIFACT_URI}"
      fi
    fi
    if [[ -z "${fe_artifact_file}" || ! -f "${fe_artifact_file}" ]]; then
      die_json "FE_ARTIFACT_MISSING" "Live staging promotion requires STAGING_FE_ARTIFACT_PATH or retrievable STAGING_FE_ARTIFACT_URI"
    fi

    if [[ -z "${fe_baseline_artifact_file}" && -n "${STAGING_FE_BASELINE_ARTIFACT_URI:-}" ]]; then
      fe_baseline_artifact_file="${STATE_DIR}/artifacts/${prev_release_id:-baseline}-fe-bundle.tar.gz"
      if ! "${GCLOUD_BIN}" storage cp "${STAGING_FE_BASELINE_ARTIFACT_URI}" "${fe_baseline_artifact_file}" >/dev/null 2>&1; then
        die_json "BASELINE_FE_ARTIFACT_RETRIEVAL_FAILED" "Failed to retrieve baseline FE artifact from ${STAGING_FE_BASELINE_ARTIFACT_URI}"
      fi
    fi
    if [[ -z "${fe_baseline_artifact_file}" || ! -f "${fe_baseline_artifact_file}" ]]; then
      die_json "BASELINE_FE_ARTIFACT_MISSING" "Live staging promotion requires STAGING_FE_BASELINE_ARTIFACT_PATH or retrievable STAGING_FE_BASELINE_ARTIFACT_URI matching rollback_baseline.frontend_artifact_sha256"
    fi
  fi

  if [[ -n "${fe_artifact_file}" ]]; then
    if [[ ! -f "${fe_artifact_file}" ]]; then
      die_json "FE_ARTIFACT_MISSING" "Configured STAGING_FE_ARTIFACT_PATH (${fe_artifact_file}) does not exist"
    fi
    observed_fe_sha="$(
      FE_PATH="${fe_artifact_file}" python3 - <<'PY'
import hashlib
import os
from pathlib import Path

digest = hashlib.sha256(Path(os.environ["FE_PATH"]).read_bytes()).hexdigest()
print(f"sha256:{digest}")
PY
    )"
  fi

  if [[ -n "${fe_baseline_artifact_file}" ]]; then
    if [[ ! -f "${fe_baseline_artifact_file}" ]]; then
      die_json "BASELINE_FE_ARTIFACT_MISSING" "Configured STAGING_FE_BASELINE_ARTIFACT_PATH (${fe_baseline_artifact_file}) does not exist"
    fi
    observed_base_fe_sha="$(
      FE_PATH="${fe_baseline_artifact_file}" python3 - <<'PY'
import hashlib
import os
from pathlib import Path

digest = hashlib.sha256(Path(os.environ["FE_PATH"]).read_bytes()).hexdigest()
print(f"sha256:{digest}")
PY
    )"
  fi

  local observed_bff_digest="${OBSERVED_BFF_IMAGE_DIGEST:-}"
  local observed_base_bff_digest="${OBSERVED_BASELINE_BFF_IMAGE_DIGEST:-}"
  if [[ "${DRY_RUN}" != "true" ]]; then
    local gcloud_img_json
    if ! gcloud_img_json="$("${GCLOUD_BIN}" artifacts docker images describe "${expected_bff_image}" --format=json 2>&1)"; then
      die_json "BFF_IMAGE_LOOKUP_FAILED" "Failed to describe immutable BFF image ${expected_bff_image}"
    fi
    if ! observed_bff_digest="$(
      IMG_JSON="${gcloud_img_json}" EXPECTED_REF="${expected_bff_image}" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["IMG_JSON"])
if not isinstance(payload, dict):
    raise SystemExit(1)
summary = payload.get("image_summary") or payload
digest = summary.get("digest") or payload.get("digest") or ""
repo_ref = os.environ["EXPECTED_REF"].split("@sha256:", 1)[0]
if not isinstance(digest, str) or not digest.startswith("sha256:") or len(digest) != 71:
    raise SystemExit(1)
print(f"{repo_ref}@{digest}")
PY
    )"; then
      die_json "BFF_IMAGE_DIGEST_UNOBSERVED" "Artifact Registry response lacked valid sha256 digest for ${expected_bff_image}"
    fi

    if [[ -n "${expected_base_bff_image}" ]]; then
      local gcloud_base_img_json
      if ! gcloud_base_img_json="$("${GCLOUD_BIN}" artifacts docker images describe "${expected_base_bff_image}" --format=json 2>&1)"; then
        die_json "BASELINE_BFF_IMAGE_LOOKUP_FAILED" "Failed to describe immutable baseline BFF image ${expected_base_bff_image}"
      fi
      if ! observed_base_bff_digest="$(
        IMG_JSON="${gcloud_base_img_json}" EXPECTED_REF="${expected_base_bff_image}" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["IMG_JSON"])
if not isinstance(payload, dict):
    raise SystemExit(1)
summary = payload.get("image_summary") or payload
digest = summary.get("digest") or payload.get("digest") or ""
repo_ref = os.environ["EXPECTED_REF"].split("@sha256:", 1)[0]
if not isinstance(digest, str) or not digest.startswith("sha256:") or len(digest) != 71:
    raise SystemExit(1)
print(f"{repo_ref}@{digest}")
PY
      )"; then
        die_json "BASELINE_BFF_IMAGE_DIGEST_UNOBSERVED" "Artifact Registry response lacked valid sha256 digest for ${expected_base_bff_image}"
      fi
    fi
  fi

  local verify_args=(
    "validate"
    "--manifest" "${RELEASE_MANIFEST_PATH}"
    "--expected-sha256" "${EXPECTED_MANIFEST_SHA256}"
    "--require-dev-verified"
  )
  if [[ -n "${observed_fe_sha}" ]]; then
    verify_args+=("--observed-fe-sha256" "${observed_fe_sha}")
  fi
  if [[ -n "${observed_bff_digest}" ]]; then
    verify_args+=("--observed-bff-digest" "${observed_bff_digest}")
  fi
  if [[ -n "${observed_base_fe_sha}" ]]; then
    verify_args+=("--observed-baseline-fe-sha256" "${observed_base_fe_sha}")
  fi
  if [[ -n "${observed_base_bff_digest}" ]]; then
    verify_args+=("--observed-baseline-bff-digest" "${observed_base_bff_digest}")
  fi

  local verified_summary
  if ! verified_summary="$(python3 "${SCRIPT_DIR}/release_manifest.py" "${verify_args[@]}" 2>&1)"; then
    die_json "ARTIFACT_CHECKSUM_MISMATCH" "Immutable artifact verification failed: $(printf '%s' "$verified_summary" | tr '\n"' '  ')"
  fi

  MANIFEST_SUMMARY="${verified_summary}" \
  DRY_RUN="${DRY_RUN}" \
  FE_ARTIFACT_FILE="${fe_artifact_file}" \
  FE_BASELINE_ARTIFACT_FILE="${fe_baseline_artifact_file}" \
  OBSERVED_FE_SHA="${observed_fe_sha:-$expected_fe_sha}" \
  OBSERVED_BFF_REF="${observed_bff_digest:-$expected_bff_image}" \
  OBSERVED_BASE_FE_SHA="${observed_base_fe_sha:-$expected_base_fe_sha}" \
  OBSERVED_BASE_BFF_REF="${observed_base_bff_digest:-$expected_base_bff_image}" \
  python3 - <<'PY'
import json
import os

summary = json.loads(os.environ["MANIFEST_SUMMARY"])
summary["dry_run"] = os.environ["DRY_RUN"] == "true"
summary["observed_fe_artifact_sha256"] = os.environ["OBSERVED_FE_SHA"]
summary["observed_operator_bff_image"] = os.environ["OBSERVED_BFF_REF"]
summary["observed_baseline_fe_artifact_sha256"] = os.environ["OBSERVED_BASE_FE_SHA"]
summary["observed_baseline_operator_bff_image"] = os.environ["OBSERVED_BASE_BFF_REF"]
summary["fe_artifact_path"] = os.environ["FE_ARTIFACT_FILE"]
summary["fe_baseline_artifact_path"] = os.environ["FE_BASELINE_ARTIFACT_FILE"]
print(json.dumps(summary, sort_keys=True))
PY
}

build_remote_env_exports() {
  python3 - <<'PY'
import os, shlex

passthrough_vars = (
    "STAGING_GCP_PROJECT_ID",
    "STAGING_ROOT",
    "STAGING_DATA_MOUNT",
    "STAGING_FE_ROOT",
    "STAGING_BFF_PORT",
    "STAGING_FE_HTTP_PORT",
    "STAGING_POSTGRES_PORT",
    "STAGING_BIN_DIR",
    "STAGING_CADDYFILE_PATH",
    "STAGING_SECRETS_ENV_FILE",
    "STAGING_POSTGRES_PASSWORD_FILE",
    "STAGING_SECRETS_GSM_NAME",
    "STAGING_POSTGRES_PASSWORD_GSM_NAME",
    "STAGING_POSTGRES_USER",
    "STAGING_POSTGRES_DB",
    "STAGING_JWT_ISSUER",
    "STAGING_JWT_AUDIENCE",
    "STAGING_IDP_ISSUER",
    "STAGING_TENANT_ID",
    "STAGING_PUBLIC_GCP_IDENTITY_API_KEY",
    "STAGING_PUBLIC_GCP_IDENTITY_PROJECT_ID",
    "STAGING_PUBLIC_GCP_IDENTITY_AUTH_DOMAIN",
    "STAGING_PUBLIC_GCP_IDENTITY_ISSUER",
    "STAGING_PUBLIC_GCP_IDENTITY_AUDIENCE",
    "STAGING_POSTGRES_IMAGE",
    "STAGING_NATS_IMAGE",
    "STAGING_ALLOW_APT_INSTALL",
    "STAGING_READY_TRIES",
    "STAGING_READY_INTERVAL",
    "STAGING_RUNTIME_CONFIG_URL",
    "STAGING_METADATA_TOKEN_URL",
    "DOCKER_BIN",
    "CURL_BIN",
    "CADDY_BIN",
    "MOUNTPOINT_BIN",
    "PSQL_BIN",
    "PG_DUMP_BIN",
    "PG_RESTORE_BIN",
    "SUDO_BIN",
    "FAKE_OSLOGIN_NONROOT",
)
parts = []
for k in passthrough_vars:
    val = os.environ.get(k)
    if val is not None and val != "":
        parts.append(f"{k}={shlex.quote(val)}")
if parts:
    print("export " + " ".join(parts) + "; ")
PY
}

case "${SUBCOMMAND}" in
  verify-artifacts)
    validate_and_inspect_artifacts
    ;;

  deploy-host)
    ARTIFACT_CHECK_JSON="$(validate_and_inspect_artifacts)"
    RELEASE_ID="$( CHECK_JSON="$ARTIFACT_CHECK_JSON" python3 -c 'import json, os; print(json.loads(os.environ["CHECK_JSON"])["release_id"])' )"
    FE_COMMIT="$( CHECK_JSON="$ARTIFACT_CHECK_JSON" python3 -c 'import json, os; print(json.loads(os.environ["CHECK_JSON"])["frontend_commit"])' )"
    FE_SHA256="$( CHECK_JSON="$ARTIFACT_CHECK_JSON" python3 -c 'import json, os; print(json.loads(os.environ["CHECK_JSON"])["frontend_artifact_sha256"])' )"
    BFF_COMMIT="$( CHECK_JSON="$ARTIFACT_CHECK_JSON" python3 -c 'import json, os; print(json.loads(os.environ["CHECK_JSON"])["backend_commit"])' )"
    BFF_IMAGE="$( CHECK_JSON="$ARTIFACT_CHECK_JSON" python3 -c 'import json, os; print(json.loads(os.environ["CHECK_JSON"])["operator_bff_image"])' )"
    FE_ARTIFACT_FILE="$( CHECK_JSON="$ARTIFACT_CHECK_JSON" python3 -c 'import json, os; print(json.loads(os.environ["CHECK_JSON"]).get("fe_artifact_path", ""))' )"
    FE_BASELINE_ARTIFACT_FILE="$( CHECK_JSON="$ARTIFACT_CHECK_JSON" python3 -c 'import json, os; print(json.loads(os.environ["CHECK_JSON"]).get("fe_baseline_artifact_path", ""))' )"
    OBSERVED_DEPLOY_FILE="${STATE_DIR}/evidence/${RELEASE_ID}-observed-deploy.json"

    if [[ "${DRY_RUN}" == "true" ]]; then
      OBSERVED_DEPLOY_FILE="${OBSERVED_DEPLOY_FILE}" \
      RELEASE_ID="${RELEASE_ID}" \
      FE_COMMIT="${FE_COMMIT}" \
      FE_SHA256="${FE_SHA256}" \
      BFF_COMMIT="${BFF_COMMIT}" \
      BFF_IMAGE="${BFF_IMAGE}" \
      python3 - <<'PY'
import json
import os
from pathlib import Path

payload = {
    "status": "dry_run_simulated",
    "dry_run": True,
    "observed": False,
    "observed_from_live_vm": False,
    "rebuilt_during_promotion": False,
    "release_id": os.environ["RELEASE_ID"],
    "frontend_commit": os.environ["FE_COMMIT"],
    "frontend_artifact_sha256": os.environ["FE_SHA256"],
    "backend_commit": os.environ["BFF_COMMIT"],
    "operator_bff_image": os.environ["BFF_IMAGE"],
}
Path(os.environ["OBSERVED_DEPLOY_FILE"]).write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
print(json.dumps(payload, sort_keys=True))
PY
      exit 0
    fi

    require_live_gcloud
    for req_var in STAGING_GCP_PROJECT_ID STAGING_GCP_ZONE VM_NAME RESERVED_IP; do
      if [[ -z "${!req_var:-}" ]]; then
        die_json "MISSING_DEPLOY_TARGET" "deploy-host requires ${req_var} in live mode"
      fi
    done

    if [[ -z "${FE_BASELINE_ARTIFACT_FILE}" || ! -f "${FE_BASELINE_ARTIFACT_FILE}" ]]; then
      die_json "BASELINE_FE_ARTIFACT_MISSING" "deploy-host requires verified baseline FE tarball matching rollback_baseline.frontend_artifact_sha256"
    fi

    REMOTE_BUNDLE_DIR="/tmp/pantheon-staging-bundle"
    STAGE_BUNDLE_DIR="$(mktemp -d "${STATE_DIR}/artifacts/bundle-${RELEASE_ID}-XXXXXX")"
    cp "${RELEASE_MANIFEST_PATH}" "${STAGE_BUNDLE_DIR}/manifest.json"
    cp "${FE_ARTIFACT_FILE}" "${STAGE_BUNDLE_DIR}/fe-dist.tar.gz"
    cp "${FE_BASELINE_ARTIFACT_FILE}" "${STAGE_BUNDLE_DIR}/fe-baseline-dist.tar.gz"
    cp "${REPO_ROOT}/deploy/staging/docker-compose.staging.yml" "${STAGE_BUNDLE_DIR}/docker-compose.staging.yml"
    cp "${REPO_ROOT}/deploy/staging/Caddyfile.staging.tmpl" "${STAGE_BUNDLE_DIR}/Caddyfile.staging.tmpl"
    cp "${REPO_ROOT}/deploy/staging/staging-env.defaults" "${STAGE_BUNDLE_DIR}/staging-env.defaults"
    cp "${REPO_ROOT}/deploy/staging/bootstrap.sh" "${STAGE_BUNDLE_DIR}/bootstrap.sh"
    cp "${REPO_ROOT}/deploy/staging/pantheon-staging-collect-gates" "${STAGE_BUNDLE_DIR}/pantheon-staging-collect-gates"
    cp "${SCRIPT_DIR}/staging_sanitize.py" "${STAGE_BUNDLE_DIR}/staging_sanitize.py"
    cp "${SCRIPT_DIR}/release_manifest.py" "${STAGE_BUNDLE_DIR}/release_manifest.py"
    chmod 0755 "${STAGE_BUNDLE_DIR}/bootstrap.sh" "${STAGE_BUNDLE_DIR}/pantheon-staging-collect-gates"

    # 1. Create remote bundle directory safely over IAP SSH before SCP transfer.
    REMOTE_MKDIR_CMD="set -euo pipefail; rm -rf '${REMOTE_BUNDLE_DIR}' && mkdir -p '${REMOTE_BUNDLE_DIR}' && chmod 0700 '${REMOTE_BUNDLE_DIR}'"
    # A freshly created VM can answer IAP 4047 (lookup) or 4003 (backend port 22 not listening yet) before sshd is up:
    # retry only those two transport codes, same args, within a short total budget that includes the command runtime.
    SSH_MKDIR_RC=0
    SSH_MKDIR_BUDGET_S=120
    SSH_MKDIR_MAX_ATTEMPTS=6
    SSH_MKDIR_START="${SECONDS}"
    for ((SSH_MKDIR_ATTEMPT = 1; SSH_MKDIR_ATTEMPT <= SSH_MKDIR_MAX_ATTEMPTS; SSH_MKDIR_ATTEMPT++)); do
      SSH_MKDIR_REMAINING=$((SSH_MKDIR_BUDGET_S - (SECONDS - SSH_MKDIR_START)))
      if [[ "${SSH_MKDIR_REMAINING}" -lt 1 ]]; then
        SSH_MKDIR_REMAINING=1
      fi
      SSH_MKDIR_RC=0
      SSH_MKDIR_OUT="$(
        timeout "${SSH_MKDIR_REMAINING}" "${GCLOUD_BIN}" compute ssh "${VM_NAME}" \
          --project="${STAGING_GCP_PROJECT_ID}" \
          --zone="${STAGING_GCP_ZONE}" \
          --tunnel-through-iap \
          --quiet \
          --command="${REMOTE_MKDIR_CMD}" 2>&1
      )" || SSH_MKDIR_RC=$?
      [[ "${SSH_MKDIR_RC}" -eq 0 ]] && break
      SSH_MKDIR_REASON=""
      if grep -q '4047' <<<"${SSH_MKDIR_OUT}" && grep -qi 'Failed to lookup instance' <<<"${SSH_MKDIR_OUT}"; then
        SSH_MKDIR_REASON="IAP 4047 Failed to lookup instance"
      elif grep -q '4003' <<<"${SSH_MKDIR_OUT}" && grep -qi 'failed to connect to backend' <<<"${SSH_MKDIR_OUT}"; then
        SSH_MKDIR_REASON="IAP 4003 failed to connect to backend port 22"
      fi
      if [[ -z "${SSH_MKDIR_REASON}" || "${SSH_MKDIR_ATTEMPT}" -eq "${SSH_MKDIR_MAX_ATTEMPTS}" || $((SECONDS - SSH_MKDIR_START + 8)) -ge "${SSH_MKDIR_BUDGET_S}" ]]; then
        break
      fi
      printf 'HOST_SSH_MKDIR_RETRY attempt=%s/%s reason=%s (rc=%s): %s\n' "${SSH_MKDIR_ATTEMPT}" "${SSH_MKDIR_MAX_ATTEMPTS}" "${SSH_MKDIR_REASON}" "${SSH_MKDIR_RC}" "$(printf '%s' "${SSH_MKDIR_OUT}" | tail -n 3 | tr '\n' ' ' | cut -c1-500)" >&2
      sleep 8
    done
    if [[ "${SSH_MKDIR_RC}" -ne 0 ]]; then
      rm -rf "${STAGE_BUNDLE_DIR}"
      # gcloud prints recommendation boilerplate after the real cause; keep the captured body (bounded) before the short JSON.
      printf 'HOST_SSH_MKDIR_FAILED diagnostic body (rc=%s, first 32768 chars):\n%s\n' "${SSH_MKDIR_RC}" "${SSH_MKDIR_OUT:0:32768}" >&2
      if [[ -n "${SSH_MKDIR_REASON:-}" ]]; then
        # Known startup error exhausted: keep bounded boot facts (no metadata values) before the caller's cleanup deletes the VM.
        printf 'HOST_SSH_MKDIR_VM_DESCRIBE (bounded):\n' >&2
        timeout 15 "${GCLOUD_BIN}" compute instances describe "${VM_NAME}" --project="${STAGING_GCP_PROJECT_ID}" --zone="${STAGING_GCP_ZONE}" \
          --format='yaml(status,tags.items,networkInterfaces[].network,networkInterfaces[].subnetwork,serviceAccounts[].scopes)' 2>&1 | head -c 4096 >&2 || true
        printf 'HOST_SSH_MKDIR_SERIAL_PORT1_TAIL (bounded):\n' >&2
        timeout 15 "${GCLOUD_BIN}" compute instances get-serial-port-output "${VM_NAME}" --project="${STAGING_GCP_PROJECT_ID}" --zone="${STAGING_GCP_ZONE}" --port=1 2>&1 | tail -n 40 | tail -c 8192 >&2 || true
      fi
      die_json "HOST_SSH_MKDIR_FAILED" "Failed to prepare ${REMOTE_BUNDLE_DIR} on ${VM_NAME} over IAP SSH (rc=${SSH_MKDIR_RC}): $(printf '%s' "$SSH_MKDIR_OUT" | tail -n 5 | tr '\n"\\' '   ' | cut -c1-1000)"
    fi

    # 2. Transfer immutable FE tarballs (candidate + baseline), manifest, compose overlay, Caddy template, bootstrap.sh, and gate collector over IAP SCP.
    if ! "${GCLOUD_BIN}" compute scp \
      --project="${STAGING_GCP_PROJECT_ID}" \
      --zone="${STAGING_GCP_ZONE}" \
      --tunnel-through-iap \
      --quiet \
      "${STAGE_BUNDLE_DIR}/manifest.json" \
      "${STAGE_BUNDLE_DIR}/fe-dist.tar.gz" \
      "${STAGE_BUNDLE_DIR}/fe-baseline-dist.tar.gz" \
      "${STAGE_BUNDLE_DIR}/docker-compose.staging.yml" \
      "${STAGE_BUNDLE_DIR}/Caddyfile.staging.tmpl" \
      "${STAGE_BUNDLE_DIR}/staging-env.defaults" \
      "${STAGE_BUNDLE_DIR}/bootstrap.sh" \
      "${STAGE_BUNDLE_DIR}/pantheon-staging-collect-gates" \
      "${STAGE_BUNDLE_DIR}/staging_sanitize.py" \
      "${STAGE_BUNDLE_DIR}/release_manifest.py" \
      "${VM_NAME}:${REMOTE_BUNDLE_DIR}/" >/dev/null 2>&1; then
      rm -rf "${STAGE_BUNDLE_DIR}"
      die_json "HOST_SCP_FAILED" "Failed to transfer immutable release bundle to ${VM_NAME} over IAP SCP"
    fi
    rm -rf "${STAGE_BUNDLE_DIR}"

    # 3. Execute remote bare-Debian bootstrap, digest-pinned pull, external runtime-config render, Compose start, and live identity readback over IAP SSH.
    BFF_HOST="${STAGING_BFF_HOST:-bff-stg-${RELEASE_ID}.pantheon.internal}"
    FE_HOST="${STAGING_FE_HOST:-stg-${RELEASE_ID}.pantheon.internal}"
    REMOTE_ENV_EXPORTS="$(build_remote_env_exports)"
    REMOTE_DEPLOY_CMD="set -euo pipefail; ${REMOTE_ENV_EXPORTS}bash '${REMOTE_BUNDLE_DIR}/bootstrap.sh' --bundle-dir '${REMOTE_BUNDLE_DIR}' --release-id '${RELEASE_ID}' --fe-sha '${FE_SHA256}' --bff-image '${BFF_IMAGE}' --bff-host '${BFF_HOST}' --fe-host '${FE_HOST}'"

    if ! SSH_DEPLOY_OUT="$(
      "${GCLOUD_BIN}" compute ssh "${VM_NAME}" \
        --project="${STAGING_GCP_PROJECT_ID}" \
        --zone="${STAGING_GCP_ZONE}" \
        --tunnel-through-iap \
        --quiet \
        --command="${REMOTE_DEPLOY_CMD}" 2>&1
    )"; then
      die_json "HOST_SSH_DEPLOY_FAILED" "Remote staging deployment command failed on ${VM_NAME}: $(printf '%s' "$SSH_DEPLOY_OUT" | tail -n 5 | tr '\n"' '  ')"
    fi

    OBSERVED_DEPLOY_FILE="${OBSERVED_DEPLOY_FILE}" \
    SSH_DEPLOY_OUT="${SSH_DEPLOY_OUT}" \
    RELEASE_ID="${RELEASE_ID}" \
    VM_NAME="${VM_NAME}" \
    RESERVED_IP="${RESERVED_IP}" \
    EXPECTED_FE_COMMIT="${FE_COMMIT}" \
    EXPECTED_FE_SHA256="${FE_SHA256}" \
    EXPECTED_BFF_COMMIT="${BFF_COMMIT}" \
    EXPECTED_BFF_IMAGE="${BFF_IMAGE}" \
    python3 - <<'PY'
import json
import os
import sys
from pathlib import Path

raw = os.environ["SSH_DEPLOY_OUT"].strip()
if not raw:
    print(json.dumps({"status": "error", "error_code": "EMPTY_HOST_DEPLOY_READBACK", "message": "SSH deploy returned empty output"}), file=sys.stderr)
    raise SystemExit(1)

observed = None
for line in reversed(raw.splitlines()):
    candidate = line.strip()
    if candidate.startswith("{") and candidate.endswith("}"):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            observed = parsed
            break

if observed is None:
    print(json.dumps({"status": "error", "error_code": "INVALID_HOST_DEPLOY_READBACK", "message": "SSH deploy returned no JSON readback"}), file=sys.stderr)
    raise SystemExit(1)

if observed.get("observed_from_live_vm") is not True or observed.get("dry_run") is not False:
    print(json.dumps({"status": "error", "error_code": "UNOBSERVED_HOST_DEPLOY", "message": "Host deploy readback is not marked observed_from_live_vm=true"}), file=sys.stderr)
    raise SystemExit(1)
if observed.get("rebuilt_during_promotion") is not False or int(observed.get("rebuild_command_count", 0)) != 0:
    print(json.dumps({"status": "error", "error_code": "REBUILD_FORBIDDEN", "message": "FE/BFF rebuild detected during promotion"}), file=sys.stderr)
    raise SystemExit(1)

fe_commit = observed.get("frontend_commit") or observed.get("frontend_commit_sha")
bff_commit = observed.get("backend_commit") or observed.get("backend_commit_sha")
checks = [
    ("frontend_commit", fe_commit, os.environ["EXPECTED_FE_COMMIT"]),
    ("frontend_artifact_sha256", observed.get("frontend_artifact_sha256"), os.environ["EXPECTED_FE_SHA256"]),
    ("backend_commit", bff_commit, os.environ["EXPECTED_BFF_COMMIT"]),
    ("operator_bff_image", observed.get("operator_bff_image"), os.environ["EXPECTED_BFF_IMAGE"]),
]
for field, actual, expected in checks:
    if actual != expected:
        print(
            json.dumps({
                "status": "error",
                "error_code": "DEPLOYED_IDENTITY_MISMATCH",
                "message": f"Observed {field} ({actual!r}) != expected ({expected!r})",
            }),
            file=sys.stderr,
        )
        raise SystemExit(1)

if observed.get("data_disk_mounted") is not True or observed.get("bff_healthy") is not True:
    print(
        json.dumps({
            "status": "error",
            "error_code": "HOST_RUNTIME_UNHEALTHY",
            "message": "Staging data disk mount or BFF health check failed on VM",
        }),
        file=sys.stderr,
    )
    raise SystemExit(1)

payload = {
    "status": "deployed_observed",
    "dry_run": False,
    "observed": True,
    "observed_from_live_vm": True,
    "rebuilt_during_promotion": False,
    "release_id": os.environ["RELEASE_ID"],
    "vm_name": os.environ["VM_NAME"],
    "reserved_ip": os.environ["RESERVED_IP"],
    "frontend_commit": fe_commit,
    "frontend_artifact_sha256": observed["frontend_artifact_sha256"],
    "backend_commit": bff_commit,
    "operator_bff_image": observed["operator_bff_image"],
    "data_disk_mounted": True,
    "bff_healthy": True,
}
Path(os.environ["OBSERVED_DEPLOY_FILE"]).write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
print(json.dumps(payload, sort_keys=True))
PY
    ;;

  collect-verify-gates)
    if [[ "${DRY_RUN}" == "true" ]]; then
      die_json "DRY_RUN_CANNOT_COLLECT_LIVE_GATES" "collect-verify-gates requires STAGING_DRY_RUN=false"
    fi
    require_live_gcloud
    for req_var in RELEASE_ID STAGING_GCP_PROJECT_ID STAGING_GCP_ZONE; do
      if [[ -z "${!req_var:-}" ]]; then
        die_json "MISSING_COLLECT_CONFIG" "collect-verify-gates requires ${req_var}"
      fi
    done

    VM_TARGET="${VM_NAME:-pantheon-stg-${RELEASE_ID}}"
    COLLECTOR_BIN="${STAGING_BIN_DIR:-/usr/local/bin}/pantheon-staging-collect-gates"
    REMOTE_ENV_EXPORTS="$(build_remote_env_exports)"
    REMOTE_VERIFY_CMD="set -euo pipefail; ${REMOTE_ENV_EXPORTS}'${COLLECTOR_BIN}' --release-id '${RELEASE_ID}'"
    if ! SSH_VERIFY_OUT="$(
      "${GCLOUD_BIN}" compute ssh "${VM_TARGET}" \
        --project="${STAGING_GCP_PROJECT_ID}" \
        --zone="${STAGING_GCP_ZONE}" \
        --tunnel-through-iap \
        --quiet \
        --command="${REMOTE_VERIFY_CMD}" 2>&1
    )"; then
      die_json "LIVE_GATE_COLLECTION_FAILED" "Failed to collect live verification gates from ${VM_TARGET} via IAP SSH: $(printf '%s' "$SSH_VERIFY_OUT" | tail -n 5 | tr '\n"' '  ')"
    fi

    GATE_OUT_DIR="${STATE_DIR}/observed-gates/${RELEASE_ID}"
    mkdir -p "${GATE_OUT_DIR}"

    SSH_VERIFY_OUT="${SSH_VERIFY_OUT}" \
    GATE_OUT_DIR="${GATE_OUT_DIR}" \
    RELEASE_ID="${RELEASE_ID}" \
    python3 - <<'PY'
import json
import os
import sys
from pathlib import Path

raw = os.environ["SSH_VERIFY_OUT"].strip()
bundle = None
for line in reversed(raw.splitlines()):
    candidate = line.strip()
    if candidate.startswith("{") and candidate.endswith("}"):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            bundle = parsed
            break

if bundle is None:
    print(json.dumps({"status": "error", "error_code": "EMPTY_GATE_COLLECTION", "message": "No JSON output from VM gate collection"}), file=sys.stderr)
    raise SystemExit(1)

if bundle.get("observed_from_live_vm") is not True or bundle.get("dry_run") is not False:
    print(json.dumps({"status": "error", "error_code": "UNOBSERVED_GATE_BUNDLE", "message": "Gate bundle was not observed from a live VM"}), file=sys.stderr)
    raise SystemExit(1)

required_sections = (
    "observed_deploy",
    "sanitized_dataset",
    "migration_plan",
    "backup_restore_record",
    "rollback_readback",
    "security_readback",
)
missing = [k for k in required_sections if not isinstance(bundle.get(k), dict)]
if missing:
    print(json.dumps({"status": "error", "error_code": "INCOMPLETE_GATE_BUNDLE", "message": f"Missing gate sections: {missing}"}), file=sys.stderr)
    raise SystemExit(1)

out_dir = Path(os.environ["GATE_OUT_DIR"])
paths = {}
for section in required_sections:
    sec_data = dict(bundle[section])
    sec_data["observed_from_live_vm"] = True
    sec_data["dry_run"] = False
    target = out_dir / f"{section.replace('_', '-')}.json"
    target.write_text(json.dumps(sec_data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    paths[section] = str(target)

print(json.dumps({
    "status": "collected",
    "release_id": os.environ["RELEASE_ID"],
    "observed_from_live_vm": True,
    "dry_run": False,
    "paths": paths,
}, sort_keys=True))
PY
    ;;

  *)
    die_json "UNKNOWN_SUBCOMMAND" "Unsupported subcommand: ${SUBCOMMAND}"
    ;;
esac
