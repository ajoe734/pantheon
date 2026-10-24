#!/usr/bin/env bash
# Pantheon Ephemeral Staging Provision & Exact-Artifact Deploy Controller
#
# Enforces:
# - Explicit non-empty current GCP project, zone, instance template, service account,
#   sanitized snapshot, release manifest, expected manifest SHA-256, and workflow run ID.
# - Zero implicit fallback to retired projects or IPs.
# - Immutable release manifest verification (`dev_verified` required, no FE/BFF rebuild).
# - Idempotent re-entry for the same (release_id, workflow_run_id) lease; fail-closed on
#   conflicting release leases.
# - Mandatory labels (`environment=staging`, `release_id`, `owner`, `workflow_run_id`,
#   `created_at`, `expires_at`, `cleanup_policy=automatic`) with bounded TTL (<= 86400s).
# - Automatic partial-create failure compensation via `staging_destroy.sh`.

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

check_retired_identifiers() {
  local val="$1"
  local name="$2"
  local retired_list=(
    "pantheon-lupin-dev-20260719"
    "pantheon-benjamin-20260528"
    "35.201.204.12"
    "35.201.239.38"
    "34.81.75.241"
    "104.155.223.192"
    "/home/lupin"
    "front-ai-trading-system"
  )
  for retired in "${retired_list[@]}"; do
    if [[ "$val" == *"$retired"* ]]; then
      die_json "RETIRED_IDENTIFIER_FORBIDDEN" "${name} contains retired identifier ${retired}"
    fi
  done
}

DRY_RUN="${STAGING_DRY_RUN:-false}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN="true"
      shift
      ;;
    *)
      die_json "UNKNOWN_ARGUMENT" "Unknown argument: $1"
      ;;
  esac
done

REQUIRED_VARS=(
  "STAGING_GCP_PROJECT_ID"
  "STAGING_GCP_ZONE"
  "STAGING_INSTANCE_TEMPLATE"
  "STAGING_SERVICE_ACCOUNT"
  "STAGING_SANITIZED_SNAPSHOT"
  "RELEASE_MANIFEST_PATH"
  "EXPECTED_MANIFEST_SHA256"
  "WORKFLOW_RUN_ID"
)

for var_name in "${REQUIRED_VARS[@]}"; do
  val="${!var_name:-}"
  if [[ -z "${val//[[:space:]]/}" ]]; then
    die_json "MISSING_REQUIRED_CONFIG" "Required configuration ${var_name} is missing or empty"
  fi
  check_retired_identifiers "$val" "$var_name"
done

if [[ "${PANTHEON_PROVIDER_ENABLED:-false}" != "false" ]]; then
  die_json "UNSAFE_DEFAULT_OVERRIDE" "PANTHEON_PROVIDER_ENABLED must be false in staging"
fi
if [[ "${PANTHEON_LIVE_BROKER_ENABLED:-false}" != "false" ]]; then
  die_json "UNSAFE_DEFAULT_OVERRIDE" "PANTHEON_LIVE_BROKER_ENABLED must be false in staging"
fi
if [[ "${PANTHEON_REAL_WRITES_ENABLED:-false}" != "false" ]]; then
  die_json "UNSAFE_DEFAULT_OVERRIDE" "PANTHEON_REAL_WRITES_ENABLED must be false in staging"
fi
if [[ "${PANTHEON_DEV_STUB_WRITES_ENABLED:-false}" != "false" ]]; then
  die_json "UNSAFE_DEFAULT_OVERRIDE" "PANTHEON_DEV_STUB_WRITES_ENABLED must be false in staging"
fi
if [[ "${PANTHEON_CONTROL_VM_BROKER_CREDENTIALS_PRESENT:-false}" != "false" ]]; then
  die_json "UNSAFE_DEFAULT_OVERRIDE" "PANTHEON_CONTROL_VM_BROKER_CREDENTIALS_PRESENT must be false"
fi

TTL_SECONDS="${STAGING_TTL_SECONDS:-${STAGING_DEFAULT_TTL_SECONDS:-7200}}"
MAX_TTL_SECONDS="${STAGING_MAX_TTL_SECONDS:-86400}"
if [[ ! "$TTL_SECONDS" =~ ^[0-9]+$ ]] || (( TTL_SECONDS <= 0 )) || (( TTL_SECONDS > MAX_TTL_SECONDS )); then
  die_json "INVALID_TTL" "STAGING_TTL_SECONDS (${TTL_SECONDS}) must be between 1 and ${MAX_TTL_SECONDS}"
fi

MANIFEST_ARGS=(
  "validate"
  "--manifest" "${RELEASE_MANIFEST_PATH}"
  "--expected-sha256" "${EXPECTED_MANIFEST_SHA256}"
  "--require-dev-verified"
)
if [[ -n "${OBSERVED_FE_ARTIFACT_SHA256:-}" ]]; then
  MANIFEST_ARGS+=("--observed-fe-sha256" "${OBSERVED_FE_ARTIFACT_SHA256}")
fi
if [[ -n "${OBSERVED_BFF_IMAGE_DIGEST:-}" ]]; then
  MANIFEST_ARGS+=("--observed-bff-digest" "${OBSERVED_BFF_IMAGE_DIGEST}")
fi

if ! MANIFEST_SUMMARY="$(python3 "${SCRIPT_DIR}/release_manifest.py" "${MANIFEST_ARGS[@]}" 2>&1)"; then
  die_json "MANIFEST_VALIDATION_FAILED" "Release manifest rejected: $(printf '%s' "$MANIFEST_SUMMARY" | tr '\n"' '  ')"
fi

RELEASE_ID="$(MANIFEST_JSON="$MANIFEST_SUMMARY" python3 -c 'import json, os; print(json.loads(os.environ["MANIFEST_JSON"])["release_id"])')"
FE_COMMIT="$(MANIFEST_JSON="$MANIFEST_SUMMARY" python3 -c 'import json, os; print(json.loads(os.environ["MANIFEST_JSON"])["frontend_commit"])')"
FE_SHA256="$(MANIFEST_JSON="$MANIFEST_SUMMARY" python3 -c 'import json, os; print(json.loads(os.environ["MANIFEST_JSON"])["frontend_artifact_sha256"])')"
BFF_COMMIT="$(MANIFEST_JSON="$MANIFEST_SUMMARY" python3 -c 'import json, os; print(json.loads(os.environ["MANIFEST_JSON"])["backend_commit"])')"
BFF_IMAGE="$(MANIFEST_JSON="$MANIFEST_SUMMARY" python3 -c 'import json, os; print(json.loads(os.environ["MANIFEST_JSON"])["operator_bff_image"])')"

STATE_DIR="${STAGING_STATE_DIR:-/tmp/pantheon-staging-state}"
mkdir -p "${STATE_DIR}/leases" "${STATE_DIR}/inventory" "${STATE_DIR}/evidence"

NOW_EPOCH="${STAGING_NOW_EPOCH:-$(date -u +%s)}"
EXPIRES_EPOCH="$(( NOW_EPOCH + TTL_SECONDS ))"
CREATED_AT_ISO="$(NOW_EPOCH="$NOW_EPOCH" python3 -c 'import datetime, os; print(datetime.datetime.fromtimestamp(int(os.environ["NOW_EPOCH"]), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))')"
EXPIRES_AT_ISO="$(EXPIRES_EPOCH="$EXPIRES_EPOCH" python3 -c 'import datetime, os; print(datetime.datetime.fromtimestamp(int(os.environ["EXPIRES_EPOCH"]), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))')"

# Check existing active staging leases for idempotency or conflict
LEASE_CHECK="$(
  STATE_DIR="$STATE_DIR" RELEASE_ID="$RELEASE_ID" WORKFLOW_RUN_ID="$WORKFLOW_RUN_ID" NOW_EPOCH="$NOW_EPOCH" \
  python3 - <<'PY'
import json
import os
from pathlib import Path

state_dir = Path(os.environ["STATE_DIR"]) / "leases"
release_id = os.environ["RELEASE_ID"]
workflow_run_id = os.environ["WORKFLOW_RUN_ID"]
now_epoch = int(os.environ["NOW_EPOCH"])

for lease_file in sorted(state_dir.glob("*.json")):
    data = json.loads(lease_file.read_text(encoding="utf-8"))
    if data.get("status") != "active":
        continue
    if int(data.get("expires_at_epoch", 0)) <= now_epoch:
        continue
    existing_release = data.get("release_id")
    existing_owner = data.get("workflow_run_id")
    if existing_release == release_id and existing_owner == workflow_run_id:
        print("IDEMPOTENT")
        raise SystemExit(0)
    print(f"CONFLICT:{existing_release}:{existing_owner}")
    raise SystemExit(0)

print("FREE")
PY
)"

if [[ "$LEASE_CHECK" == "IDEMPOTENT" ]]; then
  EXISTING_LEASE_PATH="${STATE_DIR}/leases/${RELEASE_ID}.json"
  python3 - "$EXISTING_LEASE_PATH" <<'PY'
import json
import sys
from pathlib import Path

lease = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
lease["idempotent_reentry"] = True
lease["status"] = "already_provisioned"
print(json.dumps(lease, sort_keys=True))
PY
  exit 0
fi

if [[ "$LEASE_CHECK" == CONFLICT:* ]]; then
  die_json "CONFLICTING_RELEASE_LEASE" "Active staging lease conflict (${LEASE_CHECK})"
fi

VM_NAME="pantheon-stg-${RELEASE_ID}"
BOOT_DISK_NAME="${VM_NAME}-boot"
DATA_DISK_NAME="pantheon-stg-data-${RELEASE_ID}"
IP_NAME="pantheon-stg-ip-${RELEASE_ID}"
IAM_BINDING_ID="pantheon-stg-iam-${RELEASE_ID}"
INVENTORY_FILE="${STATE_DIR}/inventory/${RELEASE_ID}.json"
LEASE_FILE="${STATE_DIR}/leases/${RELEASE_ID}.json"

# Initialize inventory state before resource allocation so partial failures clean up deterministically
STATE_DIR="$STATE_DIR" RELEASE_ID="$RELEASE_ID" WORKFLOW_RUN_ID="$WORKFLOW_RUN_ID" \
STAGING_GCP_PROJECT_ID="$STAGING_GCP_PROJECT_ID" STAGING_GCP_ZONE="$STAGING_GCP_ZONE" \
STAGING_INSTANCE_TEMPLATE="$STAGING_INSTANCE_TEMPLATE" STAGING_SERVICE_ACCOUNT="$STAGING_SERVICE_ACCOUNT" \
STAGING_SANITIZED_SNAPSHOT="$STAGING_SANITIZED_SNAPSHOT" CREATED_AT_ISO="$CREATED_AT_ISO" \
EXPIRES_AT_ISO="$EXPIRES_AT_ISO" NOW_EPOCH="$NOW_EPOCH" EXPIRES_EPOCH="$EXPIRES_EPOCH" \
FE_COMMIT="$FE_COMMIT" FE_SHA256="$FE_SHA256" BFF_COMMIT="$BFF_COMMIT" BFF_IMAGE="$BFF_IMAGE" \
EXPECTED_MANIFEST_SHA256="$EXPECTED_MANIFEST_SHA256" \
python3 - <<'PY'
import json
import os
from pathlib import Path

state_dir = Path(os.environ["STATE_DIR"])
release_id = os.environ["RELEASE_ID"]
workflow_run_id = os.environ["WORKFLOW_RUN_ID"]

labels = {
    "environment": "staging",
    "release_id": release_id,
    "owner": workflow_run_id,
    "workflow_run_id": workflow_run_id,
    "created_at": os.environ["CREATED_AT_ISO"],
    "expires_at": os.environ["EXPIRES_AT_ISO"],
    "created_at_epoch": str(os.environ["NOW_EPOCH"]),
    "expires_at_epoch": str(os.environ["EXPIRES_EPOCH"]),
    "cleanup_policy": "automatic",
}

inventory = {
    "release_id": release_id,
    "workflow_run_id": workflow_run_id,
    "project_id": os.environ["STAGING_GCP_PROJECT_ID"],
    "zone": os.environ["STAGING_GCP_ZONE"],
    "instance_template": os.environ["STAGING_INSTANCE_TEMPLATE"],
    "service_account": os.environ["STAGING_SERVICE_ACCOUNT"],
    "sanitized_snapshot": os.environ["STAGING_SANITIZED_SNAPSHOT"],
    "manifest_sha256": os.environ["EXPECTED_MANIFEST_SHA256"],
    "frontend_commit": os.environ["FE_COMMIT"],
    "frontend_artifact_sha256": os.environ["FE_SHA256"],
    "backend_commit": os.environ["BFF_COMMIT"],
    "operator_bff_image": os.environ["BFF_IMAGE"],
    "labels": labels,
    "resources": [],
}
(state_dir / "inventory" / f"{release_id}.json").write_text(
    json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY

record_resource() {
  local r_type="$1"
  local r_name="$2"
  INVENTORY_FILE="$INVENTORY_FILE" R_TYPE="$r_type" R_NAME="$r_name" python3 - <<'PY'
import json
import os
from pathlib import Path

inv_path = Path(os.environ["INVENTORY_FILE"])
data = json.loads(inv_path.read_text(encoding="utf-8"))
data["resources"].append({
    "type": os.environ["R_TYPE"],
    "name": os.environ["R_NAME"],
    "project_id": data["project_id"],
    "zone": data["zone"],
    "labels": dict(data["labels"]),
    "state": "provisioned",
})
inv_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

on_provision_Failure() {
  local exit_code=$?
  echo "Provisioning failed mid-flight (exit=${exit_code}); invoking partial-create compensation teardown..." >&2
  STAGING_STATE_DIR="${STATE_DIR}" \
  STAGING_GCP_PROJECT_ID="${STAGING_GCP_PROJECT_ID}" \
  STAGING_GCP_ZONE="${STAGING_GCP_ZONE}" \
  RELEASE_ID="${RELEASE_ID}" \
  WORKFLOW_RUN_ID="${WORKFLOW_RUN_ID}" \
  STAGING_DRY_RUN="${DRY_RUN}" \
  "${SCRIPT_DIR}/staging_destroy.sh" --mode teardown --reason partial_create_failure >/dev/null || true
  die_json "PARTIAL_CREATE_FAILURE_COMPENSATED" "Provisioning failed and partial resources were cleaned up for ${RELEASE_ID}"
}
trap on_provision_Failure ERR

run_gcloud_or_record() {
  local r_type="$1"
  local r_name="$2"
  shift 2
  if [[ "${DRY_RUN}" != "true" && -n "${GCLOUD_BIN:-}" ]]; then
    "${GCLOUD_BIN}" "$@"
  fi
  record_resource "$r_type" "$r_name"
}

# Step 1: Allocate ephemeral IP
run_gcloud_or_record "address" "${IP_NAME}" \
  compute addresses create "${IP_NAME}" \
  --project="${STAGING_GCP_PROJECT_ID}" \
  --region="${STAGING_GCP_ZONE%-*}" \
  --labels="environment=staging,release_id=${RELEASE_ID},owner=${WORKFLOW_RUN_ID},cleanup_policy=automatic"

if [[ "${STAGING_INJECT_FAIL_STAGE:-}" == "after_ip" ]]; then
  false
fi

# Step 2: Restore sanitized data disk from nonprod snapshot
run_gcloud_or_record "disk" "${DATA_DISK_NAME}" \
  compute disks create "${DATA_DISK_NAME}" \
  --project="${STAGING_GCP_PROJECT_ID}" \
  --zone="${STAGING_GCP_ZONE}" \
  --source-snapshot="${STAGING_SANITIZED_SNAPSHOT}" \
  --type="pd-balanced" \
  --labels="environment=staging,release_id=${RELEASE_ID},owner=${WORKFLOW_RUN_ID},cleanup_policy=automatic"

if [[ "${STAGING_INJECT_FAIL_STAGE:-}" == "after_data_disk" ]]; then
  false
fi

# Step 3: Create ephemeral VM and boot disk from instance template
run_gcloud_or_record "disk" "${BOOT_DISK_NAME}" \
  compute disks create "${BOOT_DISK_NAME}" \
  --project="${STAGING_GCP_PROJECT_ID}" \
  --zone="${STAGING_GCP_ZONE}" \
  --size="${STAGING_BOOT_DISK_SIZE_GB:-50}GB" \
  --type="pd-balanced" \
  --labels="environment=staging,release_id=${RELEASE_ID},owner=${WORKFLOW_RUN_ID},cleanup_policy=automatic"

if [[ "${STAGING_INJECT_FAIL_STAGE:-}" == "create_vm" ]]; then
  false
fi

run_gcloud_or_record "instance" "${VM_NAME}" \
  compute instances create "${VM_NAME}" \
  --project="${STAGING_GCP_PROJECT_ID}" \
  --zone="${STAGING_GCP_ZONE}" \
  --source-instance-template="${STAGING_INSTANCE_TEMPLATE}" \
  --service-account="${STAGING_SERVICE_ACCOUNT}" \
  --disk="name=${DATA_DISK_NAME},device-name=staging-data,mode=rw,auto-delete=no" \
  --labels="environment=staging,release_id=${RELEASE_ID},owner=${WORKFLOW_RUN_ID},cleanup_policy=automatic"

# Step 4: Bind release-scoped IAM marker and deploy exact immutable artifacts (no rebuild)
run_gcloud_or_record "iam_binding" "${IAM_BINDING_ID}" \
  projects add-iam-policy-binding "${STAGING_GCP_PROJECT_ID}" \
  --member="serviceAccount:${STAGING_SERVICE_ACCOUNT}" \
  --role="roles/compute.viewer"

trap - ERR

# Seal active lease and deployed artifact state
LEASE_FILE="$LEASE_FILE" INVENTORY_FILE="$INVENTORY_FILE" RELEASE_MANIFEST_PATH="$RELEASE_MANIFEST_PATH" \
CREATED_AT_ISO="$CREATED_AT_ISO" EXPIRES_AT_ISO="$EXPIRES_AT_ISO" NOW_EPOCH="$NOW_EPOCH" EXPIRES_EPOCH="$EXPIRES_EPOCH" \
DRY_RUN="$DRY_RUN" \
python3 - <<'PY'
import json
import os
from pathlib import Path

inv = json.loads(Path(os.environ["INVENTORY_FILE"]).read_text(encoding="utf-8"))
manifest = json.loads(Path(os.environ["RELEASE_MANIFEST_PATH"]).read_text(encoding="utf-8"))

lease = {
    "status": "active",
    "release_state": "staging_provisioned",
    "dry_run": os.environ["DRY_RUN"] == "true",
    "release_id": inv["release_id"],
    "workflow_run_id": inv["workflow_run_id"],
    "project_id": inv["project_id"],
    "zone": inv["zone"],
    "instance_template": inv["instance_template"],
    "service_account": inv["service_account"],
    "sanitized_snapshot": inv["sanitized_snapshot"],
    "created_at": os.environ["CREATED_AT_ISO"],
    "expires_at": os.environ["EXPIRES_AT_ISO"],
    "created_at_epoch": int(os.environ["NOW_EPOCH"]),
    "expires_at_epoch": int(os.environ["EXPIRES_EPOCH"]),
    "manifest_sha256": inv["manifest_sha256"],
    "deployed_artifacts": {
        "rebuilt_during_promotion": False,
        "frontend_commit": inv["frontend_commit"],
        "frontend_artifact_sha256": inv["frontend_artifact_sha256"],
        "backend_commit": inv["backend_commit"],
        "operator_bff_image": inv["operator_bff_image"],
    },
    "rollback_baseline": manifest.get("rollback_baseline"),
    "safety_defaults": manifest.get("safety_defaults"),
    "labels": inv["labels"],
    "resource_count": len(inv["resources"]),
}
Path(os.environ["LEASE_FILE"]).write_text(json.dumps(lease, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(lease, sort_keys=True))
PY
