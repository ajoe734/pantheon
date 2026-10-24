#!/usr/bin/env bash
# Pantheon Ephemeral Staging Verification Gate Controller
#
# Executes required Staging verification gates against a provisioned release:
# 1. Lease ownership, TTL liveness, and immutable FE/BFF digest readback (no rebuild).
# 2. Sanitized dataset verification (zero PII, zero secrets, nonprod origin).
# 3. Expand-phase forward migration and old-app/new-schema compatibility check.
# 4. Backup creation and restore readability rehearsal.
# 5. Exact-pair application rollback rehearsal to the baseline pair.
# 6. Tenant isolation, RBAC, and provider/write/broker-off readback checks.
# 7. Orphan cleanup dry-run and inventory reconciliation.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${STAGING_STATE_DIR:-/tmp/pantheon-staging-state}"

die_json() {
  local code="$1"
  local msg="$2"
  printf '{"status":"error","error_code":"%s","message":"%s"}\n' "$code" "$msg" >&2
  exit 1
}

REQUIRED_VARS=(
  "RELEASE_ID"
  "WORKFLOW_RUN_ID"
  "RELEASE_MANIFEST_PATH"
  "EXPECTED_MANIFEST_SHA256"
  "SANITIZED_DATASET_PATH"
  "MIGRATION_PLAN_PATH"
  "BACKUP_RESTORE_RECORD_PATH"
  "ROLLBACK_READBACK_PATH"
  "SECURITY_READBACK_PATH"
)

for var_name in "${REQUIRED_VARS[@]}"; do
  val="${!var_name:-}"
  if [[ -z "${val//[[:space:]]/}" ]]; then
    die_json "MISSING_VERIFY_INPUT" "Required verification input ${var_name} is missing"
  fi
done

LEASE_FILE="${STATE_DIR}/leases/${RELEASE_ID}.json"
INVENTORY_FILE="${STATE_DIR}/inventory/${RELEASE_ID}.json"
if [[ ! -f "$LEASE_FILE" || ! -f "$INVENTORY_FILE" ]]; then
  die_json "STAGING_NOT_PROVISIONED" "No active staging lease/inventory found for ${RELEASE_ID}"
fi

# 1. Verify immutable manifest and deployed artifact digests
MANIFEST_SUMMARY="$(
  python3 "${SCRIPT_DIR}/release_manifest.py" validate \
    --manifest "${RELEASE_MANIFEST_PATH}" \
    --expected-sha256 "${EXPECTED_MANIFEST_SHA256}" \
    --require-dev-verified
)"

LEASE_FILE="$LEASE_FILE" WORKFLOW_RUN_ID="$WORKFLOW_RUN_ID" MANIFEST_SUMMARY="$MANIFEST_SUMMARY" \
python3 - <<'PY'
import json
import os
import sys
from pathlib import Path

lease = json.loads(Path(os.environ["LEASE_FILE"]).read_text(encoding="utf-8"))
manifest = json.loads(os.environ["MANIFEST_SUMMARY"])

if lease.get("status") != "active":
    print(json.dumps({"status": "error", "error": "staging lease is not active"}), file=sys.stderr)
    raise SystemExit(1)
if lease.get("workflow_run_id") != os.environ["WORKFLOW_RUN_ID"]:
    print(json.dumps({"status": "error", "error": "staging lease workflow_run_id mismatch"}), file=sys.stderr)
    raise SystemExit(1)
deployed = lease.get("deployed_artifacts") or {}
if deployed.get("rebuilt_during_promotion") is not False:
    print(json.dumps({"status": "error", "error": "FE/BFF rebuild during promotion is forbidden"}), file=sys.stderr)
    raise SystemExit(1)
if deployed.get("frontend_artifact_sha256") != manifest["frontend_artifact_sha256"]:
    print(json.dumps({"status": "error", "error": "deployed FE digest mismatch"}), file=sys.stderr)
    raise SystemExit(1)
if deployed.get("operator_bff_image") != manifest["operator_bff_image"]:
    print(json.dumps({"status": "error", "error": "deployed BFF digest mismatch"}), file=sys.stderr)
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
  STAGING_GCP_PROJECT_ID="${STAGING_GCP_PROJECT_ID:-$(python3 -c "import json,pathlib;print(json.loads(pathlib.Path('$LEASE_FILE').read_text())['project_id'])")}" \
  STAGING_GCP_ZONE="${STAGING_GCP_ZONE:-$(python3 -c "import json,pathlib;print(json.loads(pathlib.Path('$LEASE_FILE').read_text())['zone'])")}" \
  "${SCRIPT_DIR}/staging_destroy.sh" --mode orphan-sweep --dry-run
)"

VERIFICATION_FILE="${STATE_DIR}/evidence/${RELEASE_ID}-verification.json"
mkdir -p "$(dirname "${VERIFICATION_FILE}")"

RELEASE_ID="$RELEASE_ID" WORKFLOW_RUN_ID="$WORKFLOW_RUN_ID" LEASE_FILE="$LEASE_FILE" \
VERIFICATION_FILE="$VERIFICATION_FILE" MANIFEST_SUMMARY="$MANIFEST_SUMMARY" \
SANITIZED_OUT="$SANITIZED_OUT" MIGRATION_OUT="$MIGRATION_OUT" BACKUP_OUT="$BACKUP_OUT" \
ROLLBACK_OUT="$ROLLBACK_OUT" SECURITY_OUT="$SECURITY_OUT" DRY_RUN_OUT="$DRY_RUN_OUT" \
python3 - <<'PY'
import json
import os
from pathlib import Path

lease_path = Path(os.environ["LEASE_FILE"])
lease = json.loads(lease_path.read_text(encoding="utf-8"))
lease["release_state"] = "staging_verified"
lease_path.write_text(json.dumps(lease, indent=2, sort_keys=True) + "\n", encoding="utf-8")

summary = {
    "status": "staging_verified",
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
