#!/usr/bin/env bash
# Pantheon Ephemeral Staging Provision & Exact-Artifact Deploy Controller (`ENV-STG-EPHEMERAL-IMPL-001`)
#
# Enforces:
# - Explicit non-empty current GCP project, zone, instance template, service account,
#   sanitized snapshot, release manifest, expected manifest SHA-256, and workflow run ID.
# - Zero implicit fallback to retired projects or IPs.
# - Fail-closed requirement for executable `GCLOUD_BIN` and real observed resource identities
#   outside explicit `--dry-run` (`STAGING_DRY_RUN=true`). Never records resources as
#   provisioned without executing cloud commands in live mode.
# - Immutable release manifest & source artifact verification via `staging_ops.sh verify-artifacts`
#   (`dev_verified` required, FE tarball SHA-256 + BFF `@sha256:...` digest verified, no rebuild).
# - Global/persistent lease conflict and idempotency checks across both local state and live GCP
#   labeled resources (`environment=staging`).
# - Valid GCP cloud labels (`^[a-z0-9_-]{1,63}$`) including `environment=staging`, `release_id`,
#   `owner`, `workflow_run_id`, `created_at` (compact UTC), `expires_at` (compact UTC),
#   `created_at_epoch`, `expires_at_epoch`, and `cleanup_policy=automatic` with bounded TTL (<= 86400s).
# - Genuine GCE resource attachment: reserved regional IP (`--address`), boot disk (`boot=yes,auto-delete=no`),
#   and restored sanitized data disk (`device-name=staging-data,auto-delete=no`) attached to the VM
#   and verified via readback.
# - Preexisting IAM binding detection so teardown never removes preexisting project IAM bindings.
# - Host artifact transfer, sanitized data mount, and Compose `core` deployment via `staging_ops.sh deploy-host`.
# - Automatic partial-create failure compensation via `staging_destroy.sh`.

set -Eeuo pipefail

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

STAGING_RUNTIME_SERVICE_ACCOUNT="${STAGING_RUNTIME_SERVICE_ACCOUNT:-${STAGING_SERVICE_ACCOUNT:-}}"
STAGING_SERVICE_ACCOUNT="${STAGING_RUNTIME_SERVICE_ACCOUNT}"
STAGING_DEPLOY_SERVICE_ACCOUNT="${STAGING_DEPLOY_SERVICE_ACCOUNT:-${STAGING_GCP_DEPLOY_SERVICE_ACCOUNT:-}}"

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

if [[ -n "${STAGING_DEPLOY_SERVICE_ACCOUNT}" ]]; then
  check_retired_identifiers "${STAGING_DEPLOY_SERVICE_ACCOUNT}" "STAGING_DEPLOY_SERVICE_ACCOUNT"
  if [[ "${STAGING_RUNTIME_SERVICE_ACCOUNT}" == "${STAGING_DEPLOY_SERVICE_ACCOUNT}" ]]; then
    die_json "DEPLOYER_SA_FORBIDDEN_ON_VM" "Deployer service account (${STAGING_DEPLOY_SERVICE_ACCOUNT}) must not be attached to the staging VM; use the dedicated runtime service account"
  fi
fi

if [[ "${STAGING_RUNTIME_SERVICE_ACCOUNT}" == *"github-deploy"* || "${STAGING_RUNTIME_SERVICE_ACCOUNT}" == *"-deploy@"* || "${STAGING_RUNTIME_SERVICE_ACCOUNT}" == *"deployer"* ]]; then
  die_json "DEPLOYER_SA_FORBIDDEN_ON_VM" "Service account ${STAGING_RUNTIME_SERVICE_ACCOUNT} is a deployer identity and must not be attached to the staging VM"
fi

if [[ "${DRY_RUN}" != "true" ]]; then
  if [[ -z "${GCLOUD_BIN:-}" ]]; then
    die_json "GCLOUD_BIN_REQUIRED" "Live staging provisioning (STAGING_DRY_RUN=false) requires GCLOUD_BIN to be set; refusing no-gcloud false success"
  fi
  if ! command -v "${GCLOUD_BIN}" >/dev/null 2>&1; then
    die_json "GCLOUD_BIN_NOT_EXECUTABLE" "Configured GCLOUD_BIN (${GCLOUD_BIN}) is not executable"
  fi
fi

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

STATE_DIR="${STAGING_STATE_DIR:-/tmp/pantheon-staging-state}"
mkdir -p "${STATE_DIR}/leases" "${STATE_DIR}/inventory" "${STATE_DIR}/evidence" "${STATE_DIR}/artifacts"

OPS_ARGS=("verify-artifacts")
if [[ "${DRY_RUN}" == "true" ]]; then
  OPS_ARGS+=("--dry-run")
fi
if ! MANIFEST_SUMMARY="$(
  STAGING_STATE_DIR="${STATE_DIR}" \
  STAGING_DRY_RUN="${DRY_RUN}" \
  "${SCRIPT_DIR}/staging_ops.sh" "${OPS_ARGS[@]}" 2>&1
)"; then
  die_json "MANIFEST_VALIDATION_FAILED" "Release manifest or artifact rejected: $(printf '%s' "$MANIFEST_SUMMARY" | tr '\n"' '  ')"
fi

RELEASE_ID="$(MANIFEST_JSON="$MANIFEST_SUMMARY" python3 -c 'import json, os; print(json.loads(os.environ["MANIFEST_JSON"])["release_id"])')"
FE_COMMIT="$(MANIFEST_JSON="$MANIFEST_SUMMARY" python3 -c 'import json, os; print(json.loads(os.environ["MANIFEST_JSON"])["frontend_commit"])')"
FE_SHA256="$(MANIFEST_JSON="$MANIFEST_SUMMARY" python3 -c 'import json, os; print(json.loads(os.environ["MANIFEST_JSON"])["frontend_artifact_sha256"])')"
BFF_COMMIT="$(MANIFEST_JSON="$MANIFEST_SUMMARY" python3 -c 'import json, os; print(json.loads(os.environ["MANIFEST_JSON"])["backend_commit"])')"
BFF_IMAGE="$(MANIFEST_JSON="$MANIFEST_SUMMARY" python3 -c 'import json, os; print(json.loads(os.environ["MANIFEST_JSON"])["operator_bff_image"])')"

NOW_EPOCH="${STAGING_NOW_EPOCH:-$(date -u +%s)}"
EXPIRES_EPOCH="$(( NOW_EPOCH + TTL_SECONDS ))"
CREATED_AT_ISO="$(NOW_EPOCH="$NOW_EPOCH" python3 -c 'import datetime, os; print(datetime.datetime.fromtimestamp(int(os.environ["NOW_EPOCH"]), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))')"
EXPIRES_AT_ISO="$(EXPIRES_EPOCH="$EXPIRES_EPOCH" python3 -c 'import datetime, os; print(datetime.datetime.fromtimestamp(int(os.environ["EXPIRES_EPOCH"]), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))')"
CREATED_AT_LABEL="$(NOW_EPOCH="$NOW_EPOCH" python3 -c 'import datetime, os; print(datetime.datetime.fromtimestamp(int(os.environ["NOW_EPOCH"]), datetime.timezone.utc).strftime("%Y%m%dt%H%M%Sz"))')"
EXPIRES_AT_LABEL="$(EXPIRES_EPOCH="$EXPIRES_EPOCH" python3 -c 'import datetime, os; print(datetime.datetime.fromtimestamp(int(os.environ["EXPIRES_EPOCH"]), datetime.timezone.utc).strftime("%Y%m%dt%H%M%Sz"))')"

STAGING_GCP_REGION="${STAGING_GCP_ZONE%-*}"

# Validate GCP label syntax (keys: ^[a-z][a-z0-9_-]{0,62}$, values: ^[a-z0-9_-]{1,63}$)
GCP_LABELS_CSV="$(
  RELEASE_ID="$RELEASE_ID" WORKFLOW_RUN_ID="$WORKFLOW_RUN_ID" \
  CREATED_AT_LABEL="$CREATED_AT_LABEL" EXPIRES_AT_LABEL="$EXPIRES_AT_LABEL" \
  NOW_EPOCH="$NOW_EPOCH" EXPIRES_EPOCH="$EXPIRES_EPOCH" \
  python3 - <<'PY'
import os
import re
import sys

KEY_RE = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
VAL_RE = re.compile(r"^[a-z0-9_-]{1,63}$")

labels = {
    "environment": "staging",
    "release_id": os.environ["RELEASE_ID"],
    "owner": os.environ["WORKFLOW_RUN_ID"],
    "workflow_run_id": os.environ["WORKFLOW_RUN_ID"],
    "created_at": os.environ["CREATED_AT_LABEL"],
    "expires_at": os.environ["EXPIRES_AT_LABEL"],
    "created_at_epoch": str(os.environ["NOW_EPOCH"]),
    "expires_at_epoch": str(os.environ["EXPIRES_EPOCH"]),
    "cleanup_policy": "automatic",
}

for k, v in labels.items():
    if not KEY_RE.fullmatch(k) or not VAL_RE.fullmatch(v):
        print(f"INVALID_LABEL:{k}={v}", file=sys.stderr)
        raise SystemExit(1)

print(",".join(f"{k}={v}" for k, v in sorted(labels.items())))
PY
)" || die_json "INVALID_GCP_LABELS" "Computed GCP labels failed strict GCP label syntax validation"

CLOUD_INSTANCES_JSON="[]"
CLOUD_DISKS_JSON="[]"
CLOUD_ADDRESSES_JSON="[]"
if [[ "${DRY_RUN}" != "true" ]]; then
  if ! CLOUD_INSTANCES_JSON="$("${GCLOUD_BIN}" compute instances list --project="${STAGING_GCP_PROJECT_ID}" --zones="${STAGING_GCP_ZONE}" --format=json)"; then
    die_json "CLOUD_INVENTORY_QUERY_FAILED" "Failed to query GCP instances in ${STAGING_GCP_PROJECT_ID}/${STAGING_GCP_ZONE}"
  fi
  if ! CLOUD_DISKS_JSON="$("${GCLOUD_BIN}" compute disks list --project="${STAGING_GCP_PROJECT_ID}" --zones="${STAGING_GCP_ZONE}" --format=json)"; then
    die_json "CLOUD_INVENTORY_QUERY_FAILED" "Failed to query GCP disks in ${STAGING_GCP_PROJECT_ID}/${STAGING_GCP_ZONE}"
  fi
  if ! CLOUD_ADDRESSES_JSON="$("${GCLOUD_BIN}" compute addresses list --project="${STAGING_GCP_PROJECT_ID}" --regions="${STAGING_GCP_REGION}" --format=json)"; then
    die_json "CLOUD_INVENTORY_QUERY_FAILED" "Failed to query GCP addresses in ${STAGING_GCP_PROJECT_ID}/${STAGING_GCP_REGION}"
  fi
fi

# Check existing active staging leases (both local runner state and live global GCP cloud labels)
LEASE_CHECK="$(
  STATE_DIR="$STATE_DIR" RELEASE_ID="$RELEASE_ID" WORKFLOW_RUN_ID="$WORKFLOW_RUN_ID" NOW_EPOCH="$NOW_EPOCH" \
  DRY_RUN="$DRY_RUN" CLOUD_INSTANCES_JSON="$CLOUD_INSTANCES_JSON" CLOUD_DISKS_JSON="$CLOUD_DISKS_JSON" CLOUD_ADDRESSES_JSON="$CLOUD_ADDRESSES_JSON" \
  python3 - <<'PY'
import json
import os
from pathlib import Path

state_dir = Path(os.environ["STATE_DIR"]) / "leases"
release_id = os.environ["RELEASE_ID"]
workflow_run_id = os.environ["WORKFLOW_RUN_ID"]
now_epoch = int(os.environ["NOW_EPOCH"])
dry_run = os.environ["DRY_RUN"] == "true"

idempotent_match = False

for lease_file in sorted(state_dir.glob("*.json")):
    data = json.loads(lease_file.read_text(encoding="utf-8"))
    if data.get("status") != "active":
        continue
    if int(data.get("expires_at_epoch", 0)) <= now_epoch:
        continue
    existing_release = data.get("release_id")
    existing_owner = data.get("workflow_run_id")
    if existing_release == release_id and existing_owner == workflow_run_id:
        idempotent_match = True
        continue
    print(f"CONFLICT:{existing_release}:{existing_owner}")
    raise SystemExit(0)

if not dry_run:
    for env_key in ("CLOUD_INSTANCES_JSON", "CLOUD_DISKS_JSON", "CLOUD_ADDRESSES_JSON"):
        items = json.loads(os.environ[env_key] or "[]")
        if not isinstance(items, list):
            print("INVALID_CLOUD_LIST")
            raise SystemExit(0)
        for item in items:
            labels = item.get("labels") or {}
            if labels.get("environment") != "staging" or labels.get("cleanup_policy") != "automatic":
                continue
            exp_epoch = int(labels.get("expires_at_epoch", 0) or 0)
            if exp_epoch <= now_epoch:
                continue
            c_rel = labels.get("release_id", "")
            c_owner = labels.get("workflow_run_id") or labels.get("owner") or ""
            if c_rel == release_id and c_owner == workflow_run_id:
                idempotent_match = True
                continue
            print(f"CONFLICT:{c_rel}:{c_owner}")
            raise SystemExit(0)

if idempotent_match:
    print("IDEMPOTENT")
else:
    print("FREE")
PY
)"

if [[ "$LEASE_CHECK" == "INVALID_CLOUD_LIST" ]]; then
  die_json "CLOUD_INVENTORY_INVALID_JSON" "GCP list command did not return a JSON array"
fi

if [[ "$LEASE_CHECK" == "IDEMPOTENT" ]]; then
  EXISTING_LEASE_PATH="${STATE_DIR}/leases/${RELEASE_ID}.json"
  if [[ -f "${EXISTING_LEASE_PATH}" ]]; then
    python3 - "$EXISTING_LEASE_PATH" <<'PY'
import json
import sys
from pathlib import Path

lease = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
lease["idempotent_reentry"] = True
lease["status"] = "already_provisioned"
print(json.dumps(lease, sort_keys=True))
PY
  else
    printf '{"idempotent_reentry":true,"release_id":"%s","status":"already_provisioned","workflow_run_id":"%s"}\n' "${RELEASE_ID}" "${WORKFLOW_RUN_ID}"
  fi
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
STAGING_GCP_PROJECT_ID="$STAGING_GCP_PROJECT_ID" STAGING_GCP_ZONE="$STAGING_GCP_ZONE" STAGING_GCP_REGION="$STAGING_GCP_REGION" \
STAGING_INSTANCE_TEMPLATE="$STAGING_INSTANCE_TEMPLATE" STAGING_SERVICE_ACCOUNT="$STAGING_SERVICE_ACCOUNT" \
STAGING_SANITIZED_SNAPSHOT="$STAGING_SANITIZED_SNAPSHOT" CREATED_AT_ISO="$CREATED_AT_ISO" \
EXPIRES_AT_ISO="$EXPIRES_AT_ISO" CREATED_AT_LABEL="$CREATED_AT_LABEL" EXPIRES_AT_LABEL="$EXPIRES_AT_LABEL" \
NOW_EPOCH="$NOW_EPOCH" EXPIRES_EPOCH="$EXPIRES_EPOCH" DRY_RUN="$DRY_RUN" \
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
    "created_at": os.environ["CREATED_AT_LABEL"],
    "expires_at": os.environ["EXPIRES_AT_LABEL"],
    "created_at_epoch": str(os.environ["NOW_EPOCH"]),
    "expires_at_epoch": str(os.environ["EXPIRES_EPOCH"]),
    "cleanup_policy": "automatic",
}

inventory = {
    "release_id": release_id,
    "workflow_run_id": workflow_run_id,
    "dry_run": os.environ["DRY_RUN"] == "true",
    "project_id": os.environ["STAGING_GCP_PROJECT_ID"],
    "zone": os.environ["STAGING_GCP_ZONE"],
    "region": os.environ["STAGING_GCP_REGION"],
    "instance_template": os.environ["STAGING_INSTANCE_TEMPLATE"],
    "service_account": os.environ["STAGING_SERVICE_ACCOUNT"],
    "sanitized_snapshot": os.environ["STAGING_SANITIZED_SNAPSHOT"],
    "manifest_sha256": os.environ["EXPECTED_MANIFEST_SHA256"],
    "frontend_commit": os.environ["FE_COMMIT"],
    "frontend_artifact_sha256": os.environ["FE_SHA256"],
    "backend_commit": os.environ["BFF_COMMIT"],
    "operator_bff_image": os.environ["BFF_IMAGE"],
    "created_at_iso": os.environ["CREATED_AT_ISO"],
    "expires_at_iso": os.environ["EXPIRES_AT_ISO"],
    "labels": labels,
    "resources": [],
}
(state_dir / "inventory" / f"{release_id}.json").write_text(
    json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY

on_provision_Failure() {
  local exit_code=$?
  trap - ERR
  echo "Provisioning failed mid-flight (exit=${exit_code}); invoking partial-create compensation teardown..." >&2
  STAGING_STATE_DIR="${STATE_DIR}" \
  STAGING_GCP_PROJECT_ID="${STAGING_GCP_PROJECT_ID}" \
  STAGING_GCP_ZONE="${STAGING_GCP_ZONE}" \
  RELEASE_ID="${RELEASE_ID}" \
  WORKFLOW_RUN_ID="${WORKFLOW_RUN_ID}" \
  STAGING_DRY_RUN="${DRY_RUN}" \
  GCLOUD_BIN="${GCLOUD_BIN:-}" \
  "${SCRIPT_DIR}/staging_destroy.sh" --mode teardown --reason partial_create_failure >/dev/null || true
  die_json "PARTIAL_CREATE_FAILURE_COMPENSATED" "Provisioning failed and partial resources were cleaned up for ${RELEASE_ID}"
}
trap on_provision_Failure ERR

run_gcloud_resource_create() {
  local r_type="$1"
  local r_name="$2"
  shift 2

  local cmd_out=""
  if [[ "${DRY_RUN}" != "true" ]]; then
    cmd_out="$("${GCLOUD_BIN}" "$@" --format=json)"
  fi

  INVENTORY_FILE="$INVENTORY_FILE" \
  R_TYPE="$r_type" \
  R_NAME="$r_name" \
  DRY_RUN="$DRY_RUN" \
  CMD_OUT="$cmd_out" \
  EXPECTED_BOOT_DISK="$BOOT_DISK_NAME" \
  EXPECTED_DATA_DISK="$DATA_DISK_NAME" \
  EXPECTED_RESERVED_IP="${RESERVED_IP:-}" \
  EXPECTED_RUNTIME_SA="${STAGING_RUNTIME_SERVICE_ACCOUNT}" \
  python3 - <<'PY'
import json
import os
import sys
from pathlib import Path

inv_path = Path(os.environ["INVENTORY_FILE"])
data = json.loads(inv_path.read_text(encoding="utf-8"))
r_type = os.environ["R_TYPE"]
r_name = os.environ["R_NAME"]
dry_run = os.environ["DRY_RUN"] == "true"
raw_out = os.environ["CMD_OUT"].strip()

entry = {
    "type": r_type,
    "name": r_name,
    "project_id": data["project_id"],
    "zone": data["zone"],
    "region": data["region"],
    "labels": dict(data["labels"]),
    "state": "dry_run_simulated" if dry_run else "provisioned",
    "observed": not dry_run,
}

if not dry_run:
    if not raw_out:
        print("Live gcloud create returned empty output; refusing unobserved resource", file=sys.stderr)
        raise SystemExit(1)
    parsed = json.loads(raw_out)
    if isinstance(parsed, list):
        if not parsed:
            print("Live gcloud create returned empty JSON array", file=sys.stderr)
            raise SystemExit(1)
        parsed = parsed[0]
    if not isinstance(parsed, dict):
        print("Live gcloud create did not return a JSON object", file=sys.stderr)
        raise SystemExit(1)
    res_id = str(parsed.get("id") or "").strip()
    self_link = str(parsed.get("selfLink") or "").strip()
    obs_name = str(parsed.get("name") or "").strip()
    if not res_id or not self_link or obs_name != r_name:
        print(f"Missing observed identity (id/selfLink/name) for {r_type} {r_name}", file=sys.stderr)
        raise SystemExit(1)
    entry["resource_id"] = res_id
    entry["self_link"] = self_link

    if r_type == "address":
        addr = str(parsed.get("address") or "").strip()
        if not addr:
            print(f"Reserved address {r_name} missing observed IPv4 address field", file=sys.stderr)
            raise SystemExit(1)
        entry["address"] = addr
        # GA `compute addresses create` has no --labels; labels are applied by a
        # follow-up update, so the journal keeps the create receipt until then.
        entry["state"] = "label_pending"

    elif r_type == "instance":
        expected_boot = os.environ["EXPECTED_BOOT_DISK"]
        expected_data = os.environ["EXPECTED_DATA_DISK"]
        expected_ip = os.environ["EXPECTED_RESERVED_IP"]
        expected_sa = os.environ["EXPECTED_RUNTIME_SA"]
        disks = parsed.get("disks") or []
        boot_attached = any(
            isinstance(d, dict)
            and d.get("boot") is True
            and (
                d.get("deviceName") == expected_boot
                or str(d.get("source") or "").endswith(f"/disks/{expected_boot}")
                or d.get("name") == expected_boot
            )
            for d in disks
        )
        data_attached = any(
            isinstance(d, dict)
            and (
                d.get("deviceName") == "staging-data"
                or str(d.get("source") or "").endswith(f"/disks/{expected_data}")
                or d.get("name") == expected_data
            )
            for d in disks
        )
        if not boot_attached or not data_attached:
            print(
                f"Instance {r_name} missing attached boot disk ({expected_boot}) or data disk ({expected_data})",
                file=sys.stderr,
            )
            raise SystemExit(1)

        nics = parsed.get("networkInterfaces") or []
        nat_ips = [
            ac.get("natIP")
            for nic in nics
            if isinstance(nic, dict)
            for ac in (nic.get("accessConfigs") or [])
            if isinstance(ac, dict) and ac.get("natIP")
        ]
        if not expected_ip or expected_ip not in nat_ips:
            print(
                f"Instance {r_name} NAT IP {nat_ips} does not match reserved address {expected_ip!r}",
                file=sys.stderr,
            )
            raise SystemExit(1)
        sas = parsed.get("serviceAccounts") or []
        sa_emails = [sa.get("email") for sa in sas if isinstance(sa, dict) and sa.get("email")]
        if sa_emails and expected_sa not in sa_emails:
            print(
                f"Instance {r_name} serviceAccounts {sa_emails} does not match runtime SA {expected_sa!r}",
                file=sys.stderr,
            )
            raise SystemExit(1)
        entry["attached_boot_disk"] = expected_boot
        entry["attached_data_disk"] = expected_data
        entry["nat_ip"] = expected_ip
        entry["service_account"] = expected_sa
else:
    if r_type == "address":
        entry["address"] = "198.51.100.10"
    elif r_type == "instance":
        entry["attached_boot_disk"] = os.environ["EXPECTED_BOOT_DISK"]
        entry["attached_data_disk"] = os.environ["EXPECTED_DATA_DISK"]
        entry["nat_ip"] = os.environ["EXPECTED_RESERVED_IP"]
        entry["service_account"] = os.environ["EXPECTED_RUNTIME_SA"]

data["resources"].append(entry)
inv_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

# Step 1: Allocate regional external IP
run_gcloud_resource_create "address" "${IP_NAME}" \
  compute addresses create "${IP_NAME}" \
  --project="${STAGING_GCP_PROJECT_ID}" \
  --region="${STAGING_GCP_REGION}"

# Step 1b: Label the address just created (journaled above) and verify actual labels + identity.
if [[ "${DRY_RUN}" != "true" ]]; then
  "${GCLOUD_BIN}" compute addresses update "${IP_NAME}" \
    --project="${STAGING_GCP_PROJECT_ID}" \
    --region="${STAGING_GCP_REGION}" \
    --update-labels="${GCP_LABELS_CSV}" >/dev/null
  ADDRESS_READBACK="$("${GCLOUD_BIN}" compute addresses describe "${IP_NAME}" \
    --project="${STAGING_GCP_PROJECT_ID}" \
    --region="${STAGING_GCP_REGION}" \
    --format=json)"
  INVENTORY_FILE="$INVENTORY_FILE" IP_NAME="$IP_NAME" ADDRESS_READBACK="$ADDRESS_READBACK" python3 - <<'PY'
import json
import os
import sys
from pathlib import Path

inv_path = Path(os.environ["INVENTORY_FILE"])
inv = json.loads(inv_path.read_text(encoding="utf-8"))
observed = json.loads(os.environ["ADDRESS_READBACK"])
entry = next(r for r in inv["resources"] if r["type"] == "address" and r["name"] == os.environ["IP_NAME"])
actual = observed.get("labels") or {}
missing = {k: v for k, v in inv["labels"].items() if actual.get(k) != v}
if str(observed.get("id") or "") != entry["resource_id"] or missing:
    print(f"Address {entry['name']} label readback mismatch (id/labels): {sorted(missing)}", file=sys.stderr)
    raise SystemExit(1)
entry["state"] = "provisioned"
inv_path.write_text(json.dumps(inv, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
fi

RESERVED_IP="$(
  INVENTORY_FILE="$INVENTORY_FILE" IP_NAME="$IP_NAME" python3 - <<'PY'
import json
import os
from pathlib import Path

inv = json.loads(Path(os.environ["INVENTORY_FILE"]).read_text(encoding="utf-8"))
for res in inv["resources"]:
    if res["type"] == "address" and res["name"] == os.environ["IP_NAME"]:
        print(res.get("address", ""))
        break
PY
)"

if [[ "${STAGING_INJECT_FAIL_STAGE:-}" == "after_ip" ]]; then
  false
fi

# Step 2: Restore sanitized data disk from nonprod snapshot
run_gcloud_resource_create "disk" "${DATA_DISK_NAME}" \
  compute disks create "${DATA_DISK_NAME}" \
  --project="${STAGING_GCP_PROJECT_ID}" \
  --zone="${STAGING_GCP_ZONE}" \
  --source-snapshot="${STAGING_SANITIZED_SNAPSHOT}" \
  --size="${STAGING_DATA_DISK_SIZE_GB:-100}GB" \
  --type="pd-balanced" \
  --labels="${GCP_LABELS_CSV}"

if [[ "${STAGING_INJECT_FAIL_STAGE:-}" == "after_data_disk" ]]; then
  false
fi

# Step 3: Create labeled boot disk (Debian 12 bookworm)
run_gcloud_resource_create "disk" "${BOOT_DISK_NAME}" \
  compute disks create "${BOOT_DISK_NAME}" \
  --project="${STAGING_GCP_PROJECT_ID}" \
  --zone="${STAGING_GCP_ZONE}" \
  --size="${STAGING_BOOT_DISK_SIZE_GB:-50}GB" \
  --type="pd-balanced" \
  --image-project="${STAGING_BOOT_IMAGE_PROJECT:-debian-cloud}" \
  --image-family="${STAGING_BOOT_IMAGE_FAMILY:-debian-12}" \
  --labels="${GCP_LABELS_CSV}"

if [[ "${STAGING_INJECT_FAIL_STAGE:-}" == "create_vm" ]]; then
  false
fi

# Step 4: Create ephemeral VM from template attaching reserved IP, boot disk, sanitized data disk, and runtime SA.
# Note: Project-level IAM is pre-provisioned in the staging foundation; the deploy SA only uses
# iam.serviceAccountUser on the runtime SA and never mutates project IAM bindings at provision time.
run_gcloud_resource_create "instance" "${VM_NAME}" \
  compute instances create "${VM_NAME}" \
  --project="${STAGING_GCP_PROJECT_ID}" \
  --zone="${STAGING_GCP_ZONE}" \
  --source-instance-template="${STAGING_INSTANCE_TEMPLATE}" \
  --machine-type="${STAGING_MACHINE_TYPE:-e2-standard-2}" \
  --service-account="${STAGING_RUNTIME_SERVICE_ACCOUNT}" \
  --address="${RESERVED_IP}" \
  --disk="name=${BOOT_DISK_NAME},device-name=${BOOT_DISK_NAME},boot=yes,mode=rw,auto-delete=no" \
  --disk="name=${DATA_DISK_NAME},device-name=staging-data,mode=rw,auto-delete=no" \
  --labels="${GCP_LABELS_CSV}"

if [[ "${STAGING_INJECT_FAIL_STAGE:-}" == "deploy_host" ]]; then
  false
fi

# Step 5: Stage 2 DEPLOY — Transfer immutable FE/BFF artifacts, bootstrap bare Debian, mount data disk, and start Compose core
DEPLOY_HOST_ARGS=("deploy-host")
if [[ "${DRY_RUN}" == "true" ]]; then
  DEPLOY_HOST_ARGS+=("--dry-run")
fi

DEPLOY_HOST_OUT="$(
  STAGING_STATE_DIR="${STATE_DIR}" \
  STAGING_DRY_RUN="${DRY_RUN}" \
  STAGING_GCP_PROJECT_ID="${STAGING_GCP_PROJECT_ID}" \
  STAGING_GCP_ZONE="${STAGING_GCP_ZONE}" \
  VM_NAME="${VM_NAME}" \
  RESERVED_IP="${RESERVED_IP}" \
  RELEASE_MANIFEST_PATH="${RELEASE_MANIFEST_PATH}" \
  EXPECTED_MANIFEST_SHA256="${EXPECTED_MANIFEST_SHA256}" \
  GCLOUD_BIN="${GCLOUD_BIN:-}" \
  "${SCRIPT_DIR}/staging_ops.sh" "${DEPLOY_HOST_ARGS[@]}"
)"

trap - ERR

# Seal active lease and deployed artifact state
LEASE_FILE="$LEASE_FILE" INVENTORY_FILE="$INVENTORY_FILE" RELEASE_MANIFEST_PATH="$RELEASE_MANIFEST_PATH" \
CREATED_AT_ISO="$CREATED_AT_ISO" EXPIRES_AT_ISO="$EXPIRES_AT_ISO" NOW_EPOCH="$NOW_EPOCH" EXPIRES_EPOCH="$EXPIRES_EPOCH" \
DRY_RUN="$DRY_RUN" DEPLOY_HOST_OUT="$DEPLOY_HOST_OUT" RESERVED_IP="$RESERVED_IP" VM_NAME="$VM_NAME" \
BOOT_DISK_NAME="$BOOT_DISK_NAME" DATA_DISK_NAME="$DATA_DISK_NAME" IP_NAME="$IP_NAME" \
python3 - <<'PY'
import json
import os
from pathlib import Path

inv = json.loads(Path(os.environ["INVENTORY_FILE"]).read_text(encoding="utf-8"))
manifest = json.loads(Path(os.environ["RELEASE_MANIFEST_PATH"]).read_text(encoding="utf-8"))
deploy_out = json.loads(os.environ["DEPLOY_HOST_OUT"])
dry_run = os.environ["DRY_RUN"] == "true"

lease = {
    "status": "active",
    "release_state": "staging_dry_run_provisioned" if dry_run else "staging_provisioned",
    "dry_run": dry_run,
    "observed": not dry_run,
    "release_id": inv["release_id"],
    "workflow_run_id": inv["workflow_run_id"],
    "project_id": inv["project_id"],
    "zone": inv["zone"],
    "region": inv["region"],
    "instance_template": inv["instance_template"],
    "service_account": inv["service_account"],
    "sanitized_snapshot": inv["sanitized_snapshot"],
    "vm_name": os.environ["VM_NAME"],
    "boot_disk_name": os.environ["BOOT_DISK_NAME"],
    "data_disk_name": os.environ["DATA_DISK_NAME"],
    "ip_name": os.environ["IP_NAME"],
    "reserved_ip": os.environ["RESERVED_IP"],
    "created_at": os.environ["CREATED_AT_ISO"],
    "expires_at": os.environ["EXPIRES_AT_ISO"],
    "created_at_epoch": int(os.environ["NOW_EPOCH"]),
    "expires_at_epoch": int(os.environ["EXPIRES_EPOCH"]),
    "manifest_sha256": inv["manifest_sha256"],
    "deployed_artifacts": {
        "rebuilt_during_promotion": False,
        "observed_from_live_vm": bool(deploy_out.get("observed_from_live_vm")),
        "frontend_commit": deploy_out["frontend_commit"],
        "frontend_artifact_sha256": deploy_out["frontend_artifact_sha256"],
        "backend_commit": deploy_out["backend_commit"],
        "operator_bff_image": deploy_out["operator_bff_image"],
    },
    "rollback_baseline": manifest.get("rollback_baseline"),
    "safety_defaults": manifest.get("safety_defaults"),
    "labels": inv["labels"],
    "resource_count": len(inv["resources"]),
}
Path(os.environ["LEASE_FILE"]).write_text(json.dumps(lease, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(lease, sort_keys=True))
PY
