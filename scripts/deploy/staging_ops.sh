#!/usr/bin/env bash
# Pantheon Ephemeral Staging Host & Immutable Artifact Adapter (`ENV-STG-EPHEMERAL-IMPL-001`)
#
# Subcommands:
# - `verify-artifacts`: Validates the immutable release manifest (`dev_verified` required),
#   retrieves/hashes the FE artifact bundle (`STAGING_FE_ARTIFACT_PATH` or `STAGING_FE_ARTIFACT_URI`),
#   queries the BFF container digest (`@sha256:...`) via `GCLOUD_BIN` in live mode, and fails closed
#   on any missing artifact or checksum/digest mismatch without rebuilding FE or BFF.
# - `deploy-host`: Transfers immutable FE/BFF artifacts, compose overlay, Caddy template, and
#   sanitized data verifier to the provisioned ephemeral staging VM via `GCLOUD_BIN compute scp`,
#   mounts the attached sanitized data disk, starts the `core` Compose profile with `--no-build`
#   and `pull_policy: never`, and records live observed deployment identity from the VM.
# - `collect-verify-gates`: Executes and collects real verification gate readbacks from the live
#   staging VM via `GCLOUD_BIN compute ssh` (observed deployed digests, sanitized Postgres dataset,
#   expand-phase forward migration & old-app/new-schema smoke, backup/restore readability query,
#   exact-pair rollback rehearsal, and tenant/RBAC + safe-off runtime readbacks).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DEFAULTS_FILE="${REPO_ROOT}/deploy/staging/staging-env.defaults"

if [[ -f "${DEFAULTS_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${DEFAULTS_FILE}"
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

  local release_id expected_fe_sha expected_bff_image
  release_id="$(MANIFEST_JSON="$base_summary" python3 -c 'import json, os; print(json.loads(os.environ["MANIFEST_JSON"])["release_id"])')"
  expected_fe_sha="$(MANIFEST_JSON="$base_summary" python3 -c 'import json, os; print(json.loads(os.environ["MANIFEST_JSON"])["frontend_artifact_sha256"])')"
  expected_bff_image="$(MANIFEST_JSON="$base_summary" python3 -c 'import json, os; print(json.loads(os.environ["MANIFEST_JSON"])["operator_bff_image"])')"

  local observed_fe_sha="${OBSERVED_FE_ARTIFACT_SHA256:-}"
  local fe_artifact_file="${STAGING_FE_ARTIFACT_PATH:-}"

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

  local observed_bff_digest="${OBSERVED_BFF_IMAGE_DIGEST:-}"
  if [[ "${DRY_RUN}" != "true" ]]; then
    local gcloud_img_json
    if ! gcloud_img_json="$("${GCLOUD_BIN}" artifacts docker images describe "${expected_bff_image}" --format=json 2>&1)"; then
      die_json "BFF_IMAGE_LOOKUP_FAILED" "Failed to describe immutable BFF image ${expected_bff_image}"
    fi
    if ! observed_bff_digest="$(
      IMG_JSON="${gcloud_img_json}" EXPECTED_REF="${expected_bff_image}" python3 - <<'PY'
import json
import os
import sys

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

  local verified_summary
  if ! verified_summary="$(python3 "${SCRIPT_DIR}/release_manifest.py" "${verify_args[@]}" 2>&1)"; then
    die_json "ARTIFACT_CHECKSUM_MISMATCH" "Immutable artifact verification failed: $(printf '%s' "$verified_summary" | tr '\n"' '  ')"
  fi

  MANIFEST_SUMMARY="${verified_summary}" \
  DRY_RUN="${DRY_RUN}" \
  FE_ARTIFACT_FILE="${fe_artifact_file}" \
  OBSERVED_FE_SHA="${observed_fe_sha:-$expected_fe_sha}" \
  OBSERVED_BFF_REF="${observed_bff_digest:-$expected_bff_image}" \
  python3 - <<'PY'
import json
import os

summary = json.loads(os.environ["MANIFEST_SUMMARY"])
summary["dry_run"] = os.environ["DRY_RUN"] == "true"
summary["observed_fe_artifact_sha256"] = os.environ["OBSERVED_FE_SHA"]
summary["observed_operator_bff_image"] = os.environ["OBSERVED_BFF_REF"]
summary["fe_artifact_path"] = os.environ["FE_ARTIFACT_FILE"]
print(json.dumps(summary, sort_keys=True))
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

    # Transfer immutable FE bundle, manifest, compose overlay, and Caddy template to the staging VM
    if ! "${GCLOUD_BIN}" compute scp \
      --project="${STAGING_GCP_PROJECT_ID}" \
      --zone="${STAGING_GCP_ZONE}" \
      --quiet \
      "${RELEASE_MANIFEST_PATH}" \
      "${FE_ARTIFACT_FILE}" \
      "${REPO_ROOT}/deploy/staging/docker-compose.staging.yml" \
      "${REPO_ROOT}/deploy/staging/Caddyfile.staging.tmpl" \
      "${REPO_ROOT}/deploy/staging/staging-env.defaults" \
      "${VM_NAME}:/tmp/pantheon-staging-bundle/" >/dev/null 2>&1; then
      die_json "HOST_SCP_FAILED" "Failed to transfer immutable release bundle to ${VM_NAME}"
    fi

    # Execute remote bootstrap, data-disk mount, FE atomic symlink, Caddy render, and Compose core start (no rebuild)
    REMOTE_DEPLOY_CMD="set -euo pipefail; \
      export PANTHEON_ENV=staging \
             PANTHEON_RELEASE_ID='${RELEASE_ID}' \
             PANTHEON_OPERATOR_BFF_IMAGE='${BFF_IMAGE}' \
             PANTHEON_PROVIDER_ENABLED=false \
             PANTHEON_LIVE_BROKER_ENABLED=false \
             PANTHEON_REAL_WRITES_ENABLED=false \
             PANTHEON_DEV_STUB_WRITES_ENABLED=false \
             PANTHEON_EXECUTION_MODE=paper \
             VITE_BFF_MODE=live \
             VITE_BFF_FALLBACK=strict \
             STAGING_BFF_CORS_ORIGINS='${STAGING_FE_ORIGIN:-https://stg-${RELEASE_ID}.internal}'; \
      sudo mkdir -p /var/lib/pantheon-staging /var/www/pantheon-staging-fe/releases/'${RELEASE_ID}'; \
      if [[ -b /dev/disk/by-id/google-staging-data ]] && ! mountpoint -q /var/lib/pantheon-staging; then \
        sudo mount /dev/disk/by-id/google-staging-data /var/lib/pantheon-staging; \
      fi; \
      sudo tar -xzf '/tmp/pantheon-staging-bundle/$(basename "${FE_ARTIFACT_FILE}")' -C '/var/www/pantheon-staging-fe/releases/${RELEASE_ID}'; \
      sudo ln -sfn '/var/www/pantheon-staging-fe/releases/${RELEASE_ID}' /var/www/pantheon-staging-fe/current; \
      docker compose -f /tmp/pantheon-staging-bundle/docker-compose.staging.yml --profile core up -d --no-build"

    if ! SSH_DEPLOY_OUT="$(
      "${GCLOUD_BIN}" compute ssh "${VM_NAME}" \
        --project="${STAGING_GCP_PROJECT_ID}" \
        --zone="${STAGING_GCP_ZONE}" \
        --quiet \
        --command="${REMOTE_DEPLOY_CMD}" 2>&1
    )"; then
      die_json "HOST_SSH_DEPLOY_FAILED" "Remote staging deployment command failed on ${VM_NAME}"
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

lines = [line.strip() for line in raw.splitlines() if line.strip().startswith("{")]
if not lines:
    print(json.dumps({"status": "error", "error_code": "INVALID_HOST_DEPLOY_READBACK", "message": "SSH deploy returned no JSON readback"}), file=sys.stderr)
    raise SystemExit(1)

observed = json.loads(lines[-1])
if observed.get("observed_from_live_vm") is not True or observed.get("dry_run") is not False:
    print(json.dumps({"status": "error", "error_code": "UNOBSERVED_HOST_DEPLOY", "message": "Host deploy readback is not marked observed_from_live_vm=true"}), file=sys.stderr)
    raise SystemExit(1)
if observed.get("rebuilt_during_promotion") is not False:
    print(json.dumps({"status": "error", "error_code": "REBUILD_FORBIDDEN", "message": "FE/BFF rebuild detected during promotion"}), file=sys.stderr)
    raise SystemExit(1)

checks = [
    ("frontend_commit", observed.get("frontend_commit"), os.environ["EXPECTED_FE_COMMIT"]),
    ("frontend_artifact_sha256", observed.get("frontend_artifact_sha256"), os.environ["EXPECTED_FE_SHA256"]),
    ("backend_commit", observed.get("backend_commit"), os.environ["EXPECTED_BFF_COMMIT"]),
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
    "frontend_commit": observed["frontend_commit"],
    "frontend_artifact_sha256": observed["frontend_artifact_sha256"],
    "backend_commit": observed["backend_commit"],
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
    REMOTE_VERIFY_CMD="set -euo pipefail; pantheon-staging-collect-gates --release-id '${RELEASE_ID}'"
    if ! SSH_VERIFY_OUT="$(
      "${GCLOUD_BIN}" compute ssh "${VM_TARGET}" \
        --project="${STAGING_GCP_PROJECT_ID}" \
        --zone="${STAGING_GCP_ZONE}" \
        --quiet \
        --command="${REMOTE_VERIFY_CMD}" 2>&1
    )"; then
      die_json "LIVE_GATE_COLLECTION_FAILED" "Failed to collect live verification gates from ${VM_TARGET} via SSH"
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
lines = [line.strip() for line in raw.splitlines() if line.strip().startswith("{")]
if not lines:
    print(json.dumps({"status": "error", "error_code": "EMPTY_GATE_COLLECTION", "message": "No JSON output from VM gate collection"}), file=sys.stderr)
    raise SystemExit(1)

bundle = json.loads(lines[-1])
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
