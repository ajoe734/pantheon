#!/usr/bin/env bash
# Pantheon Ephemeral Staging Verification Gate Controller (`ENV-STG-EPHEMERAL-IMPL-001`)
#
# Executes required Staging verification gates against a provisioned release:
# 1. Lease ownership, TTL liveness, and live-VM observed FE/BFF digest readback (no rebuild).
#    Refuses dry-run or unobserved leases/fixtures from producing `staging_verified`.
# 2. Sanitized dataset verification (zero PII, zero secrets, nonprod origin).
# 3. Expand-phase forward migration and old-app/new-schema compatibility check.
# 4. Backup creation and restore readability rehearsal.
# 5. Exact-pair application rollback rehearsal to the baseline pair.
# 6. Tenant isolation, RBAC, and provider/write/broker-off readback checks.
# 7. Orphan cleanup dry-run and inventory reconciliation.
#
# Evidence output shape aligns with `scripts/deploy/prod_preflight.py` (`check_staging_evidence`).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${STAGING_STATE_DIR:-/tmp/pantheon-staging-state}"

die_json() {
  local code="$1"
  local msg="$2"
  printf '{"status":"error","error_code":"%s","message":"%s"}\n' "$code" "$msg" >&2
  exit 1
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

BASE_REQUIRED_VARS=(
  "RELEASE_ID"
  "WORKFLOW_RUN_ID"
  "RELEASE_MANIFEST_PATH"
  "EXPECTED_MANIFEST_SHA256"
)

for var_name in "${BASE_REQUIRED_VARS[@]}"; do
  val="${!var_name:-}"
  if [[ -z "${val//[[:space:]]/}" ]]; then
    die_json "MISSING_VERIFY_INPUT" "Required verification input ${var_name} is missing"
  fi
done

if [[ "${DRY_RUN}" != "true" ]]; then
  if [[ -z "${GCLOUD_BIN:-}" ]]; then
    die_json "GCLOUD_BIN_REQUIRED" "Live staging verification (STAGING_DRY_RUN=false) requires GCLOUD_BIN; refusing no-gcloud false success"
  fi
  if ! command -v "${GCLOUD_BIN}" >/dev/null 2>&1; then
    die_json "GCLOUD_BIN_NOT_EXECUTABLE" "Configured GCLOUD_BIN (${GCLOUD_BIN}) is not executable"
  fi
fi

LEASE_FILE="${STATE_DIR}/leases/${RELEASE_ID}.json"
INVENTORY_FILE="${STATE_DIR}/inventory/${RELEASE_ID}.json"
OBSERVED_DEPLOY_FILE="${STATE_DIR}/evidence/${RELEASE_ID}-observed-deploy.json"

if [[ ! -f "$LEASE_FILE" || ! -f "$INVENTORY_FILE" ]]; then
  die_json "STAGING_NOT_PROVISIONED" "No active staging lease/inventory found for ${RELEASE_ID}"
fi

PROJECT_ID="${STAGING_GCP_PROJECT_ID:-$(python3 -c "import json,pathlib;print(json.loads(pathlib.Path('$LEASE_FILE').read_text()).get('project_id',''))")}"
ZONE="${STAGING_GCP_ZONE:-$(python3 -c "import json,pathlib;print(json.loads(pathlib.Path('$LEASE_FILE').read_text()).get('zone',''))")}"

# In live mode, refuse dry-run leases and collect/validate real VM outputs via staging_ops.sh
LIVE_OBSERVED_DEPLOY_PATH="${OBSERVED_DEPLOY_FILE}"
if [[ "${DRY_RUN}" != "true" ]]; then
  LEASE_CHECK_ERR="$(
    LEASE_FILE="$LEASE_FILE" OBSERVED_DEPLOY_FILE="$OBSERVED_DEPLOY_FILE" python3 - <<'PY'
import json
import os
from pathlib import Path

lease = json.loads(Path(os.environ["LEASE_FILE"]).read_text(encoding="utf-8"))
if lease.get("dry_run") is True or lease.get("observed") is not True:
    print("DRY_RUN_LEASE_REJECTED:Cannot produce live staging_verified from a dry-run or unobserved lease")
    raise SystemExit(0)
obs_path = Path(os.environ["OBSERVED_DEPLOY_FILE"])
if not obs_path.exists():
    print("MISSING_OBSERVED_DEPLOY:Missing observed host deployment record")
    raise SystemExit(0)
obs = json.loads(obs_path.read_text(encoding="utf-8"))
if obs.get("observed_from_live_vm") is not True or obs.get("dry_run") is not False:
    print("UNOBSERVED_DEPLOY_REJECTED:Host deployment record is not observed from a live VM")
    raise SystemExit(0)
print("OK")
PY
  )"
  if [[ "$LEASE_CHECK_ERR" != "OK" ]]; then
    die_json "${LEASE_CHECK_ERR%%:*}" "${LEASE_CHECK_ERR#*:}"
  fi

  # If any gate path was explicitly supplied in live mode, verify it is not an unobserved static fixture
  for gate_var in SANITIZED_DATASET_PATH MIGRATION_PLAN_PATH BACKUP_RESTORE_RECORD_PATH ROLLBACK_READBACK_PATH SECURITY_READBACK_PATH; do
    gate_val="${!gate_var:-}"
    if [[ -n "${gate_val}" ]]; then
      if [[ ! -f "${gate_val}" ]]; then
        die_json "MISSING_VERIFY_INPUT" "Supplied ${gate_var} (${gate_val}) does not exist"
      fi
      FIXTURE_CHECK="$(
        GATE_FILE="${gate_val}" python3 - <<'PY'
import json
import os
from pathlib import Path

data = json.loads(Path(os.environ["GATE_FILE"]).read_text(encoding="utf-8"))
if data.get("observed_from_live_vm") is not True or data.get("dry_run") is not False:
    print("UNOBSERVED")
else:
    print("OBSERVED")
PY
      )"
      if [[ "${FIXTURE_CHECK}" != "OBSERVED" ]]; then
        die_json "UNOBSERVED_FIXTURE_REJECTED" "Live staging verification refuses unobserved/dry-run fixture input in ${gate_var}"
      fi
    fi
  done

  # Collect live verification gates from the staging VM via staging_ops.sh when not already populated
  if [[ -z "${SANITIZED_DATASET_PATH:-}" || -z "${MIGRATION_PLAN_PATH:-}" || -z "${BACKUP_RESTORE_RECORD_PATH:-}" || -z "${ROLLBACK_READBACK_PATH:-}" || -z "${SECURITY_READBACK_PATH:-}" ]]; then
    COLLECTED_JSON="$(
      STAGING_STATE_DIR="${STATE_DIR}" \
      STAGING_DRY_RUN="false" \
      RELEASE_ID="${RELEASE_ID}" \
      STAGING_GCP_PROJECT_ID="${PROJECT_ID}" \
      STAGING_GCP_ZONE="${ZONE}" \
      RELEASE_MANIFEST_PATH="${RELEASE_MANIFEST_PATH}" \
      EXPECTED_MANIFEST_SHA256="${EXPECTED_MANIFEST_SHA256}" \
      GCLOUD_BIN="${GCLOUD_BIN}" \
      "${SCRIPT_DIR}/staging_ops.sh" collect-verify-gates
    )"
    LIVE_OBSERVED_DEPLOY_PATH="$(COLLECTED_JSON="$COLLECTED_JSON" python3 -c 'import json, os; print(json.loads(os.environ["COLLECTED_JSON"])["paths"]["observed_deploy"])')"
    SANITIZED_DATASET_PATH="${SANITIZED_DATASET_PATH:-$(COLLECTED_JSON="$COLLECTED_JSON" python3 -c 'import json, os; print(json.loads(os.environ["COLLECTED_JSON"])["paths"]["sanitized_dataset"])')}"
    MIGRATION_PLAN_PATH="${MIGRATION_PLAN_PATH:-$(COLLECTED_JSON="$COLLECTED_JSON" python3 -c 'import json, os; print(json.loads(os.environ["COLLECTED_JSON"])["paths"]["migration_plan"])')}"
    BACKUP_RESTORE_RECORD_PATH="${BACKUP_RESTORE_RECORD_PATH:-$(COLLECTED_JSON="$COLLECTED_JSON" python3 -c 'import json, os; print(json.loads(os.environ["COLLECTED_JSON"])["paths"]["backup_restore_record"])')}"
    ROLLBACK_READBACK_PATH="${ROLLBACK_READBACK_PATH:-$(COLLECTED_JSON="$COLLECTED_JSON" python3 -c 'import json, os; print(json.loads(os.environ["COLLECTED_JSON"])["paths"]["rollback_readback"])')}"
    SECURITY_READBACK_PATH="${SECURITY_READBACK_PATH:-$(COLLECTED_JSON="$COLLECTED_JSON" python3 -c 'import json, os; print(json.loads(os.environ["COLLECTED_JSON"])["paths"]["security_readback"])')}"
  fi
else
  for gate_var in SANITIZED_DATASET_PATH MIGRATION_PLAN_PATH BACKUP_RESTORE_RECORD_PATH ROLLBACK_READBACK_PATH SECURITY_READBACK_PATH; do
    gate_val="${!gate_var:-}"
    if [[ -z "${gate_val//[[:space:]]/}" ]]; then
      die_json "MISSING_VERIFY_INPUT" "Dry-run verification requires fixture input ${gate_var}"
    fi
  done
fi

# 1. Verify immutable manifest and live observed deployed artifact digests (not self-written lease fields)
OBSERVED_FE_SHA="$(
  OBS_FILE="${LIVE_OBSERVED_DEPLOY_PATH}" LEASE_FILE="${LEASE_FILE}" DRY_RUN="${DRY_RUN}" python3 - <<'PY'
import json
import os
from pathlib import Path

obs_path = Path(os.environ["OBS_FILE"])
if obs_path.exists():
    data = json.loads(obs_path.read_text(encoding="utf-8"))
    print(data.get("frontend_artifact_sha256", ""))
else:
    lease = json.loads(Path(os.environ["LEASE_FILE"]).read_text(encoding="utf-8"))
    print((lease.get("deployed_artifacts") or {}).get("frontend_artifact_sha256", ""))
PY
)"

OBSERVED_BFF_IMAGE="$(
  OBS_FILE="${LIVE_OBSERVED_DEPLOY_PATH}" LEASE_FILE="${LEASE_FILE}" DRY_RUN="${DRY_RUN}" python3 - <<'PY'
import json
import os
from pathlib import Path

obs_path = Path(os.environ["OBS_FILE"])
if obs_path.exists():
    data = json.loads(obs_path.read_text(encoding="utf-8"))
    print(data.get("operator_bff_image", ""))
else:
    lease = json.loads(Path(os.environ["LEASE_FILE"]).read_text(encoding="utf-8"))
    print((lease.get("deployed_artifacts") or {}).get("operator_bff_image", ""))
PY
)"

MANIFEST_SUMMARY="$(
  python3 "${SCRIPT_DIR}/release_manifest.py" validate \
    --manifest "${RELEASE_MANIFEST_PATH}" \
    --expected-sha256 "${EXPECTED_MANIFEST_SHA256}" \
    --observed-fe-sha256 "${OBSERVED_FE_SHA}" \
    --observed-bff-digest "${OBSERVED_BFF_IMAGE}" \
    --require-dev-verified
)"

LEASE_FILE="$LEASE_FILE" OBS_FILE="$LIVE_OBSERVED_DEPLOY_PATH" WORKFLOW_RUN_ID="$WORKFLOW_RUN_ID" \
MANIFEST_SUMMARY="$MANIFEST_SUMMARY" DRY_RUN="$DRY_RUN" \
python3 - <<'PY'
import json
import os
import sys
from pathlib import Path

lease = json.loads(Path(os.environ["LEASE_FILE"]).read_text(encoding="utf-8"))
manifest = json.loads(os.environ["MANIFEST_SUMMARY"])
dry_run = os.environ["DRY_RUN"] == "true"
obs_path = Path(os.environ["OBS_FILE"])
obs = json.loads(obs_path.read_text(encoding="utf-8")) if obs_path.exists() else {}

if lease.get("status") != "active":
    print(json.dumps({"status": "error", "error": "staging lease is not active"}), file=sys.stderr)
    raise SystemExit(1)
if lease.get("workflow_run_id") != os.environ["WORKFLOW_RUN_ID"]:
    print(json.dumps({"status": "error", "error": "staging lease workflow_run_id mismatch"}), file=sys.stderr)
    raise SystemExit(1)

if not dry_run:
    if obs.get("observed_from_live_vm") is not True or obs.get("dry_run") is not False:
        print(json.dumps({"status": "error", "error": "live verification requires observed_from_live_vm=True"}), file=sys.stderr)
        raise SystemExit(1)
    if obs.get("rebuilt_during_promotion") is not False:
        print(json.dumps({"status": "error", "error": "FE/BFF rebuild during promotion is forbidden"}), file=sys.stderr)
        raise SystemExit(1)
    if obs.get("frontend_commit") != manifest["frontend_commit"]:
        print(json.dumps({"status": "error", "error": "observed FE commit mismatch"}), file=sys.stderr)
        raise SystemExit(1)
    if obs.get("backend_commit") != manifest["backend_commit"]:
        print(json.dumps({"status": "error", "error": "observed BFF commit mismatch"}), file=sys.stderr)
        raise SystemExit(1)
    if obs.get("frontend_artifact_sha256") != manifest["frontend_artifact_sha256"]:
        print(json.dumps({"status": "error", "error": "observed FE digest mismatch"}), file=sys.stderr)
        raise SystemExit(1)
    if obs.get("operator_bff_image") != manifest["operator_bff_image"]:
        print(json.dumps({"status": "error", "error": "observed BFF digest mismatch"}), file=sys.stderr)
        raise SystemExit(1)
PY

# 2. Sanitized dataset check
SANITIZED_OUT="$(python3 "${SCRIPT_DIR}/staging_sanitize.py" verify-sanitized --input "${SANITIZED_DATASET_PATH}")"

# 3. Forward migration & old-app/new-schema compatibility
MIGRATION_OUT="$(python3 "${SCRIPT_DIR}/staging_sanitize.py" verify-migration --input "${MIGRATION_PLAN_PATH}")"

# 4. Backup & restore readability rehearsal
BACKUP_OUT="$(python3 "${SCRIPT_DIR}/staging_sanitize.py" verify-backup-restore --input "${BACKUP_RESTORE_RECORD_PATH}")"

# 5. Exact-pair rollback rehearsal
BASELINE_TMP="$(mktemp)"
RELEASE_MANIFEST_PATH="$RELEASE_MANIFEST_PATH" BASELINE_TMP="$BASELINE_TMP" python3 - <<'PY'
import json
import os
from pathlib import Path

manifest = json.loads(Path(os.environ["RELEASE_MANIFEST_PATH"]).read_text(encoding="utf-8"))
Path(os.environ["BASELINE_TMP"]).write_text(
    json.dumps(manifest["rollback_baseline"], sort_keys=True), encoding="utf-8"
)
PY
ROLLBACK_OUT="$(
  python3 "${SCRIPT_DIR}/staging_sanitize.py" verify-rollback \
    --baseline "${BASELINE_TMP}" \
    --readback "${ROLLBACK_READBACK_PATH}"
)"
rm -f "${BASELINE_TMP}"

# 6. Tenant isolation, RBAC, and provider/write/broker-off readbacks
SECURITY_OUT="$(python3 "${SCRIPT_DIR}/staging_sanitize.py" verify-readbacks --input "${SECURITY_READBACK_PATH}")"

# 7. Cleanup dry-run & inventory reconciliation
DRY_RUN_OUT="$(
  STAGING_STATE_DIR="${STATE_DIR}" \
  STAGING_ORPHAN_CLEANUP_ENABLED="true" \
  STAGING_GCP_PROJECT_ID="${PROJECT_ID}" \
  STAGING_GCP_ZONE="${ZONE}" \
  GCLOUD_BIN="${GCLOUD_BIN:-}" \
  "${SCRIPT_DIR}/staging_destroy.sh" --mode orphan-sweep --dry-run
)"

VERIFICATION_FILE="${STATE_DIR}/evidence/${RELEASE_ID}-verification.json"
mkdir -p "$(dirname "${VERIFICATION_FILE}")"

RELEASE_ID="$RELEASE_ID" WORKFLOW_RUN_ID="$WORKFLOW_RUN_ID" LEASE_FILE="$LEASE_FILE" \
VERIFICATION_FILE="$VERIFICATION_FILE" MANIFEST_SUMMARY="$MANIFEST_SUMMARY" DRY_RUN="$DRY_RUN" \
SANITIZED_OUT="$SANITIZED_OUT" MIGRATION_OUT="$MIGRATION_OUT" BACKUP_OUT="$BACKUP_OUT" \
ROLLBACK_OUT="$ROLLBACK_OUT" SECURITY_OUT="$SECURITY_OUT" DRY_RUN_OUT="$DRY_RUN_OUT" \
python3 - <<'PY'
import json
import os
from pathlib import Path

dry_run = os.environ["DRY_RUN"] == "true"
lease_path = Path(os.environ["LEASE_FILE"])
lease = json.loads(lease_path.read_text(encoding="utf-8"))

final_status = "dry_run_validated" if dry_run else "staging_verified"
lease["release_state"] = "staging_dry_run_validated" if dry_run else "staging_verified"
lease_path.write_text(json.dumps(lease, indent=2, sort_keys=True) + "\n", encoding="utf-8")

summary = {
    "status": final_status,
    "dry_run": dry_run,
    "observed_from_live_vm": not dry_run,
    "release_id": os.environ["RELEASE_ID"],
    "workflow_run_id": os.environ["WORKFLOW_RUN_ID"],
    "manifest": json.loads(os.environ["MANIFEST_SUMMARY"]),
    "gates": {
        "sanitized_data": json.loads(os.environ["SANITIZED_OUT"]),
        "migration_and_compatibility": json.loads(os.environ["MIGRATION_OUT"]),
        "backup_restore_readability": json.loads(os.environ["BACKUP_OUT"]),
        "exact_pair_rollback": json.loads(os.environ["ROLLBACK_OUT"]),
        "security_and_safety_readbacks": json.loads(os.environ["SECURITY_OUT"]),
        "cleanup_dry_run": json.loads(os.environ["DRY_RUN_OUT"]),
    },
}
Path(os.environ["VERIFICATION_FILE"]).write_text(
    json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
print(json.dumps(summary, sort_keys=True))
PY
