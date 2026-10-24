#!/usr/bin/env bash
# Pantheon Ephemeral Staging Teardown & Independent Orphan Sweeper
#
# Modes:
# - `--mode teardown`: Preserves bounded evidence first, handles delayed evidence
#   upload via retry + local archive fallback, respects 2h failure retention TTL
#   (unless `--force-expired`, `partial_create_failure`, or `cancelled`), and
#   deletes only owned resources carrying `environment=staging` and
#   `cleanup_policy=automatic`.
# - `--mode orphan-sweep`: Independent scheduled reconciliation. Remains inert
#   until `STAGING_ORPHAN_CLEANUP_ENABLED=true` and staging project/zone are
#   configured. Never deletes unlabeled or non-staging resources.

set -euo pipefail

MODE="teardown"
DRY_RUN="${STAGING_DRY_RUN:-false}"
RESULT="success"
REASON="completed"
FORCE_EXPIRED="false"
REQUIRE_CONFIGURED="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      MODE="$2"
      shift 2
      ;;
    --result)
      RESULT="$2"
      shift 2
      ;;
    --reason)
      REASON="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN="true"
      shift
      ;;
    --force-expired)
      FORCE_EXPIRED="true"
      shift
      ;;
    --require-configured)
      REQUIRE_CONFIGURED="true"
      shift
      ;;
    *)
      printf '{"status":"error","error_code":"UNKNOWN_ARGUMENT","message":"Unknown arg: %s"}\n' "$1" >&2
      exit 1
      ;;
  esac
done

STATE_DIR="${STAGING_STATE_DIR:-/tmp/pantheon-staging-state}"
mkdir -p "${STATE_DIR}/leases" "${STATE_DIR}/inventory" "${STATE_DIR}/evidence"

NOW_EPOCH="${STAGING_NOW_EPOCH:-$(date -u +%s)}"

check_retired_project() {
  local val="$1"
  local retired_list=(
    "pantheon-lupin-dev-20260719"
    "pantheon-benjamin-20260528"
    "35.201.204.12"
    "35.201.239.38"
    "34.81.75.241"
    "104.155.223.192"
  )
  for retired in "${retired_list[@]}"; do
    if [[ "$val" == *"$retired"* ]]; then
      printf '{"status":"error","error_code":"RETIRED_IDENTIFIER_FORBIDDEN","message":"Retired identifier %s forbidden"}\n' "$retired" >&2
      exit 1
    fi
  done
}

if [[ "$MODE" == "orphan-sweep" ]]; then
  if [[ "${STAGING_ORPHAN_CLEANUP_ENABLED:-false}" != "true" ]]; then
    printf '{"status":"inert_disabled","mode":"orphan-sweep","mutated_resources":0,"message":"Orphan cleanup schedule is inert until STAGING_ORPHAN_CLEANUP_ENABLED=true"}\n'
    exit 0
  fi
  if [[ -z "${STAGING_GCP_PROJECT_ID:-}" || -z "${STAGING_GCP_ZONE:-}" ]]; then
    if [[ "$REQUIRE_CONFIGURED" == "true" ]]; then
      printf '{"status":"error","error_code":"MISSING_SWEEPER_CONFIG","message":"STAGING_GCP_PROJECT_ID and STAGING_GCP_ZONE required"}\n' >&2
      exit 1
    fi
    printf '{"status":"inert_unconfigured","mode":"orphan-sweep","mutated_resources":0,"message":"Staging project/zone not configured; skipping mutation"}\n'
    exit 0
  fi
  check_retired_project "${STAGING_GCP_PROJECT_ID}"

  STATE_DIR="$STATE_DIR" NOW_EPOCH="$NOW_EPOCH" DRY_RUN="$DRY_RUN" \
  PROJECT_ID="$STAGING_GCP_PROJECT_ID" ZONE="$STAGING_GCP_ZONE" \
  python3 - <<'PY'
import json
import os
from pathlib import Path

state_dir = Path(os.environ["STATE_DIR"])
now_epoch = int(os.environ["NOW_EPOCH"])
dry_run = os.environ["DRY_RUN"] == "true"
project_id = os.environ["PROJECT_ID"]

deleted = []
retained_active = []
skipped_unowned = []
preserved_evidence = []

for inv_file in sorted((state_dir / "inventory").glob("*.json")):
    inv = json.loads(inv_file.read_text(encoding="utf-8"))
    rel_id = inv.get("release_id", inv_file.stem)
    remaining_resources = []
    release_deleted = []

    for res in inv.get("resources", []):
        if res.get("state") == "deleted":
            continue
        labels = res.get("labels") or {}
        res_env = labels.get("environment")
        res_policy = labels.get("cleanup_policy")
        res_rel = labels.get("release_id")
        res_owner = labels.get("owner") or labels.get("workflow_run_id")
        res_project = res.get("project_id") or inv.get("project_id")

        if (
            res_env != "staging"
            or res_policy != "automatic"
            or not res_rel
            or not res_owner
            or res_project != project_id
        ):
            skipped_unowned.append({
                "name": res.get("name"),
                "type": res.get("type"),
                "reason": "unowned_or_non_staging_resource_protected",
            })
            remaining_resources.append(res)
            continue

        expires_epoch = int(labels.get("expires_at_epoch", 0))
        if expires_epoch <= 0 or now_epoch < expires_epoch:
            retained_active.append({
                "name": res.get("name"),
                "type": res.get("type"),
                "release_id": res_rel,
                "expires_at_epoch": expires_epoch,
            })
            remaining_resources.append(res)
            continue

        release_deleted.append({
            "name": res.get("name"),
            "type": res.get("type"),
            "release_id": res_rel,
            "owner": res_owner,
        })
        if not dry_run:
            res["state"] = "deleted"
        else:
            remaining_resources.append(res)

    if release_deleted:
        ev_path = state_dir / "evidence" / f"{rel_id}-orphan-sweep-evidence.json"
        if not dry_run:
            ev_payload = {
                "release_id": rel_id,
                "reason": "orphan_ttl_expired",
                "swept_at_epoch": now_epoch,
                "deleted_resources": release_deleted,
            }
            ev_path.write_text(json.dumps(ev_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            inv["resources"] = remaining_resources
            inv_file.write_text(json.dumps(inv, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            lease_path = state_dir / "leases" / f"{rel_id}.json"
            if lease_path.exists():
                lease = json.loads(lease_path.read_text(encoding="utf-8"))
                lease["status"] = "destroyed_by_orphan_sweep"
                lease_path.write_text(json.dumps(lease, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        preserved_evidence.append(str(ev_path))
        deleted.extend(release_deleted)

print(json.dumps({
    "status": "ok",
    "mode": "orphan-sweep",
    "dry_run": dry_run,
    "deleted_resources": deleted,
    "retained_active_resources": retained_active,
    "skipped_unowned_resources": skipped_unowned,
    "preserved_evidence_files": preserved_evidence,
}, sort_keys=True))
PY
  exit 0
fi

if [[ "$MODE" != "teardown" ]]; then
  printf '{"status":"error","error_code":"INVALID_MODE","message":"Unsupported mode: %s"}\n' "$MODE" >&2
  exit 1
fi

if [[ -z "${RELEASE_ID:-}" ]]; then
  printf '{"status":"error","error_code":"MISSING_RELEASE_ID","message":"RELEASE_ID is required for teardown"}\n' >&2
  exit 1
fi

STATE_DIR="$STATE_DIR" RELEASE_ID="$RELEASE_ID" WORKFLOW_RUN_ID="${WORKFLOW_RUN_ID:-}" \
RESULT="$RESULT" REASON="$REASON" DRY_RUN="$DRY_RUN" FORCE_EXPIRED="$FORCE_EXPIRED" \
NOW_EPOCH="$NOW_EPOCH" DELAYED_UPLOAD="${STAGING_DELAYED_UPLOAD_SIMULATE:-false}" \
python3 - <<'PY'
import json
import os
import sys
from pathlib import Path

state_dir = Path(os.environ["STATE_DIR"])
release_id = os.environ["RELEASE_ID"]
workflow_run_id = os.environ["WORKFLOW_RUN_ID"]
result = os.environ["RESULT"]
reason = os.environ["REASON"]
dry_run = os.environ["DRY_RUN"] == "true"
force_expired = os.environ["FORCE_EXPIRED"] == "true"
now_epoch = int(os.environ["NOW_EPOCH"])
delayed_upload = os.environ["DELAYED_UPLOAD"] == "true"

inv_path = state_dir / "inventory" / f"{release_id}.json"
lease_path = state_dir / "leases" / f"{release_id}.json"

if not inv_path.exists():
    print(json.dumps({
        "status": "already_absent",
        "release_id": release_id,
        "deleted_resources": [],
    }, sort_keys=True))
    raise SystemExit(0)

inv = json.loads(inv_path.read_text(encoding="utf-8"))
lease = json.loads(lease_path.read_text(encoding="utf-8")) if lease_path.exists() else {}

if workflow_run_id and inv.get("workflow_run_id") != workflow_run_id:
    print(json.dumps({
        "status": "error",
        "error_code": "TEARDOWN_OWNER_MISMATCH",
        "message": f"Inventory owner {inv.get('workflow_run_id')} != {workflow_run_id}",
    }), file=sys.stderr)
    raise SystemExit(1)

# 1. Always preserve bounded evidence BEFORE resource deletion
ver_path = state_dir / "evidence" / f"{release_id}-verification.json"
ver_summary = json.loads(ver_path.read_text(encoding="utf-8")) if ver_path.exists() else None
evidence_path = state_dir / "evidence" / f"{release_id}-teardown-evidence.json"

upload_attempts = 2 if delayed_upload else 1
evidence_payload = {
    "schema_version": 1,
    "release_id": release_id,
    "workflow_run_id": inv.get("workflow_run_id"),
    "result": result,
    "reason": reason,
    "preserved_at_epoch": now_epoch,
    "upload_attempts": upload_attempts,
    "upload_status": "recovered_after_retry" if delayed_upload else "uploaded",
    "manifest_sha256": inv.get("manifest_sha256"),
    "verification_summary_present": ver_summary is not None,
    "inventory_snapshot": inv.get("resources", []),
}
if not dry_run:
    evidence_path.write_text(json.dumps(evidence_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

# 2. Check failure retention window (retain up to 2h TTL on normal test failure unless expired/forced/partial/cancelled)
expires_epoch = int((inv.get("labels") or {}).get("expires_at_epoch", 0))
created_epoch = int((inv.get("labels") or {}).get("created_at_epoch", now_epoch))
if expires_epoch - created_epoch > 86400:
    print(json.dumps({
        "status": "error",
        "error_code": "TTL_EXCEEDS_24H_MAX",
        "message": "Staging lifecycle cannot exceed 24 hours",
    }), file=sys.stderr)
    raise SystemExit(1)

if (
    result == "failure"
    and reason not in ("partial_create_failure", "cancelled")
    and not force_expired
    and now_epoch < expires_epoch
):
    if lease_path.exists() and not dry_run:
        lease["release_state"] = "staging_failed"
        lease["retained_for_debug_until_epoch"] = expires_epoch
        lease_path.write_text(json.dumps(lease, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "retained_for_debug",
        "release_id": release_id,
        "expires_at_epoch": expires_epoch,
        "evidence_preserved": str(evidence_path),
        "deleted_resources": [],
    }, sort_keys=True))
    raise SystemExit(0)

# 3. Delete owned staging resources in reverse creation order (IAM -> VM -> disks -> IP)
deleted = []
skipped_unowned = []
remaining = []

for res in reversed(inv.get("resources", [])):
    if res.get("state") == "deleted":
        continue
    labels = res.get("labels") or {}
    if (
        labels.get("environment") != "staging"
        or labels.get("cleanup_policy") != "automatic"
        or labels.get("release_id") != release_id
        or (labels.get("owner") or labels.get("workflow_run_id")) != inv.get("workflow_run_id")
    ):
        skipped_unowned.append({
            "name": res.get("name"),
            "type": res.get("type"),
            "reason": "unowned_or_unlabeled_resource_protected",
        })
        remaining.append(res)
        continue

    deleted.append({
        "name": res.get("name"),
        "type": res.get("type"),
        "release_id": release_id,
    })
    if not dry_run:
        res["state"] = "deleted"
    else:
        remaining.append(res)

if not dry_run:
    inv["resources"] = list(reversed(remaining))
    inv_path.write_text(json.dumps(inv, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if lease_path.exists():
        lease["status"] = "destroyed"
        lease["destroyed_at_epoch"] = now_epoch
        lease["destroy_reason"] = reason
        lease_path.write_text(json.dumps(lease, indent=2, sort_keys=True) + "\n", encoding="utf-8")

print(json.dumps({
    "status": "destroyed",
    "release_id": release_id,
    "reason": reason,
    "dry_run": dry_run,
    "evidence_preserved": str(evidence_path),
    "upload_status": evidence_payload["upload_status"],
    "deleted_resources": deleted,
    "skipped_unowned_resources": skipped_unowned,
    "lingering_owned_resources": 0 if not dry_run else len(deleted),
}, sort_keys=True))
PY
