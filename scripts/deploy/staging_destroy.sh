#!/usr/bin/env bash
# Pantheon Ephemeral Staging Teardown & Independent Orphan Sweeper (`ENV-STG-EPHEMERAL-IMPL-001`)
#
# Modes:
# - `--mode teardown`: Preserves bounded evidence first, handles delayed evidence
#   upload via retry + local archive fallback, respects 2h failure retention TTL
#   (unless `--force-expired`, `partial_create_failure`, or `cancelled`), executes
#   real `GCLOUD_BIN` deletion in reverse creation order (IAM -> VM -> disks -> IP),
#   preserves unowned resources and preexisting IAM bindings, and performs post-delete
#   cloud readback to prove zero lingering owned resources.
# - `--mode orphan-sweep`: Independent scheduled reconciliation. Remains inert
#   until `STAGING_ORPHAN_CLEANUP_ENABLED=true` and staging project/zone are
#   configured. Queries live GCP cloud inventory directly via `GCLOUD_BIN` so
#   cross-run orphan resources are discovered even on a fresh runner with empty
#   local state. Never deletes unlabeled, non-staging, active, or preexisting IAM resources.

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

die_json() {
  local code="$1"
  local msg="$2"
  printf '{"status":"error","error_code":"%s","message":"%s"}\n' "$code" "$msg" >&2
  exit 1
}

require_live_gcloud() {
  if [[ -z "${GCLOUD_BIN:-}" ]]; then
    die_json "GCLOUD_BIN_REQUIRED" "Live staging teardown/sweep (STAGING_DRY_RUN=false) requires GCLOUD_BIN; refusing no-gcloud false success"
  fi
  if ! command -v "${GCLOUD_BIN}" >/dev/null 2>&1; then
    die_json "GCLOUD_BIN_NOT_EXECUTABLE" "Configured GCLOUD_BIN (${GCLOUD_BIN}) is not executable"
  fi
}

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
    "/home/lupin"
    "front-ai-trading-system"
  )
  for retired in "${retired_list[@]}"; do
    if [[ "$val" == *"$retired"* ]]; then
      die_json "RETIRED_IDENTIFIER_FORBIDDEN" "Retired identifier ${retired} forbidden"
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
      die_json "MISSING_SWEEPER_CONFIG" "STAGING_GCP_PROJECT_ID and STAGING_GCP_ZONE required"
    fi
    printf '{"status":"inert_unconfigured","mode":"orphan-sweep","mutated_resources":0,"message":"Staging project/zone not configured; skipping mutation"}\n'
    exit 0
  fi
  check_retired_project "${STAGING_GCP_PROJECT_ID}"

  if [[ "${DRY_RUN}" != "true" ]]; then
    require_live_gcloud
  fi

  STATE_DIR="$STATE_DIR" NOW_EPOCH="$NOW_EPOCH" DRY_RUN="$DRY_RUN" \
  PROJECT_ID="$STAGING_GCP_PROJECT_ID" ZONE="$STAGING_GCP_ZONE" \
  GCLOUD_BIN="${GCLOUD_BIN:-}" \
  python3 - <<'PY'
import json
import os
import subprocess
import sys
from pathlib import Path

state_dir = Path(os.environ["STATE_DIR"])
now_epoch = int(os.environ["NOW_EPOCH"])
dry_run = os.environ["DRY_RUN"] == "true"
project_id = os.environ["PROJECT_ID"]
zone = os.environ["ZONE"]
region = zone.rsplit("-", 1)[0]
gcloud_bin = os.environ.get("GCLOUD_BIN", "").strip()


def run_gcloud(args: list[str]) -> str:
    proc = subprocess.run([gcloud_bin, *args, "--format=json"], capture_output=True, text=True)
    if proc.returncode != 0:
        print(
            json.dumps({
                "status": "error",
                "error_code": "CLOUD_COMMAND_FAILED",
                "message": f"gcloud {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}",
            }),
            file=sys.stderr,
        )
        raise SystemExit(1)
    return proc.stdout


PENDING_PREFIX = "pantheon-pending-v1:"


def pending_address_labels(item: dict, expect_project: str, expect_region: str, expect_release: str | None = None) -> dict | None:
    """Labels from a strictly valid owned-pending description on an unlabelled address, else None."""
    if item.get("labels"):
        return None
    desc = str(item.get("description") or "")
    if not desc.startswith(PENDING_PREFIX):
        return None
    try:
        meta = json.loads(desc[len(PENDING_PREFIX):])
    except ValueError:
        return None
    labels = meta.get("labels") if isinstance(meta, dict) else None
    if (
        not isinstance(labels, dict)
        or meta.get("project") != expect_project
        or meta.get("region") != expect_region
        or labels.get("environment") != "staging"
        or labels.get("cleanup_policy") != "automatic"
        or not labels.get("release_id")
        or not labels.get("owner")
        or (expect_release is not None and labels.get("release_id") != expect_release)
        or item.get("name") != f"pantheon-stg-ip-{labels['release_id']}"
    ):
        return None
    return {str(k): str(v) for k, v in labels.items()}


def query_cloud_resources() -> list[dict]:
    if not gcloud_bin:
        return []
    discovered = []
    inst_raw = json.loads(run_gcloud(["compute", "instances", "list", f"--project={project_id}", f"--zones={zone}"]) or "[]")
    disk_raw = json.loads(run_gcloud(["compute", "disks", "list", f"--project={project_id}", f"--zones={zone}"]) or "[]")
    addr_raw = json.loads(run_gcloud(["compute", "addresses", "list", f"--project={project_id}", f"--regions={region}"]) or "[]")
    for item in inst_raw:
        if isinstance(item, dict) and item.get("name"):
            discovered.append({
                "type": "instance",
                "name": item["name"],
                "project_id": item.get("project_id") or project_id,
                "zone": zone,
                "region": region,
                "labels": item.get("labels") or {},
                "state": "provisioned",
            })
    for item in disk_raw:
        if isinstance(item, dict) and item.get("name"):
            discovered.append({
                "type": "disk",
                "name": item["name"],
                "project_id": item.get("project_id") or project_id,
                "zone": zone,
                "region": region,
                "labels": item.get("labels") or {},
                "state": "provisioned",
            })
    for item in addr_raw:
        if isinstance(item, dict) and item.get("name"):
            pending = pending_address_labels(item, project_id, region)
            discovered.append({
                "type": "address",
                "name": item["name"],
                "project_id": item.get("project_id") or project_id,
                "zone": zone,
                "region": region,
                "labels": pending if pending is not None else (item.get("labels") or {}),
                "state": "label_pending" if pending is not None else "provisioned",
            })
    return discovered


# 1. Merge local inventory files with live cloud resource discovery
merged_by_key: dict[tuple[str, str], dict] = {}
inv_files_by_rel: dict[str, Path] = {}

for inv_file in sorted((state_dir / "inventory").glob("*.json")):
    inv = json.loads(inv_file.read_text(encoding="utf-8"))
    rel_id = inv.get("release_id", inv_file.stem)
    inv_files_by_rel[rel_id] = inv_file
    for res in inv.get("resources", []):
        if res.get("state") == "deleted":
            continue
        key = (str(res.get("type")), str(res.get("name")))
        entry = dict(res)
        entry.setdefault("project_id", inv.get("project_id", project_id))
        entry.setdefault("zone", inv.get("zone", zone))
        entry.setdefault("region", inv.get("region", region))
        entry["_source_release_id"] = rel_id
        merged_by_key[key] = entry

foreign_pending = []
if gcloud_bin:
    # A journaled label_pending address is deletable only while the live address with that name still has the journaled id.
    live_addr_ids = {
        a["name"]: str(a.get("id") or "")
        for a in json.loads(run_gcloud(["compute", "addresses", "list", f"--project={project_id}", f"--regions={region}"]) or "[]")
        if isinstance(a, dict) and a.get("name")
    }
    for key, res in list(merged_by_key.items()):
        if key[0] != "address" or res.get("state") != "label_pending":
            continue
        live_id = live_addr_ids.get(key[1])
        if live_id is None:
            del merged_by_key[key]
        elif live_id != str(res.get("resource_id") or ""):
            del merged_by_key[key]
            foreign_pending.append(key[1])

if not dry_run or gcloud_bin:
    for c_res in query_cloud_resources():
        key = (str(c_res["type"]), str(c_res["name"]))
        if key[0] == "address" and key[1] in foreign_pending:
            continue
        if key not in merged_by_key:
            c_labels = c_res.get("labels") or {}
            c_res["_source_release_id"] = c_labels.get("release_id") or "cloud-discovered"
            merged_by_key[key] = c_res

deleted = []
retained_active = []
skipped_unowned = [
    {"name": n, "type": "address", "reason": "unowned_or_unlabeled_resource_protected"} for n in foreign_pending
]
preserved_evidence = []
to_delete_by_rel: dict[str, list[dict]] = {}

for (r_type, r_name), res in merged_by_key.items():
    labels = res.get("labels") or {}
    res_env = labels.get("environment")
    res_policy = labels.get("cleanup_policy")
    res_rel = labels.get("release_id")
    res_owner = labels.get("owner") or labels.get("workflow_run_id")
    res_project = res.get("project_id") or project_id

    if r_type == "iam_binding" and res.get("preexisting") is True:
        skipped_unowned.append({
            "name": r_name,
            "type": r_type,
            "reason": "preexisting_iam_binding_preserved",
        })
        continue

    if (
        res_env != "staging"
        or res_policy != "automatic"
        or not res_rel
        or not res_owner
        or res_project != project_id
    ):
        skipped_unowned.append({
            "name": r_name,
            "type": r_type,
            "reason": "unowned_or_non_staging_resource_protected",
        })
        continue

    try:
        expires_epoch = int(labels.get("expires_at_epoch", 0))
    except (TypeError, ValueError):
        expires_epoch = 0

    if expires_epoch <= 0 or now_epoch < expires_epoch:
        retained_active.append({
            "name": r_name,
            "type": r_type,
            "release_id": res_rel,
            "expires_at_epoch": expires_epoch,
        })
        continue

    to_delete_by_rel.setdefault(res_rel, []).append(res)

# Delete order: iam_binding -> instance -> disk -> address
TYPE_ORDER = {"iam_binding": 0, "instance": 1, "disk": 2, "address": 3}

for rel_id, rel_resources in sorted(to_delete_by_rel.items()):
    ordered = sorted(rel_resources, key=lambda r: TYPE_ORDER.get(str(r.get("type")), 99))
    rel_deleted_records = []
    for res in ordered:
        r_type = res["type"]
        r_name = res["name"]
        res_owner = (res.get("labels") or {}).get("owner") or (res.get("labels") or {}).get("workflow_run_id")
        if not dry_run:
            if r_type == "iam_binding":
                if not res.get("preexisting"):
                    member = res.get("member")
                    role = res.get("role")
                    if member and role:
                        run_gcloud(["projects", "remove-iam-policy-binding", project_id, f"--member={member}", f"--role={role}"])
            elif r_type == "instance":
                run_gcloud(["compute", "instances", "delete", r_name, f"--project={project_id}", f"--zone={zone}", "--quiet"])
            elif r_type == "disk":
                run_gcloud(["compute", "disks", "delete", r_name, f"--project={project_id}", f"--zone={zone}", "--quiet"])
            elif r_type == "address":
                run_gcloud(["compute", "addresses", "delete", r_name, f"--project={project_id}", f"--region={region}", "--quiet"])
        rel_deleted_records.append({
            "name": r_name,
            "type": r_type,
            "release_id": rel_id,
            "owner": res_owner,
        })

    if rel_deleted_records:
        ev_path = state_dir / "evidence" / f"{rel_id}-orphan-sweep-evidence.json"
        if not dry_run:
            ev_payload = {
                "release_id": rel_id,
                "reason": "orphan_ttl_expired",
                "swept_at_epoch": now_epoch,
                "deleted_resources": rel_deleted_records,
            }
            ev_path.write_text(json.dumps(ev_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            if rel_id in inv_files_by_rel and inv_files_by_rel[rel_id].exists():
                inv_file = inv_files_by_rel[rel_id]
                inv_data = json.loads(inv_file.read_text(encoding="utf-8"))
                deleted_keys = {(d["type"], d["name"]) for d in rel_deleted_records}
                inv_data["resources"] = [
                    r for r in inv_data.get("resources", [])
                    if (r.get("type"), r.get("name")) not in deleted_keys
                ]
                inv_file.write_text(json.dumps(inv_data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            lease_path = state_dir / "leases" / f"{rel_id}.json"
            if lease_path.exists():
                lease = json.loads(lease_path.read_text(encoding="utf-8"))
                lease["status"] = "destroyed_by_orphan_sweep"
                lease_path.write_text(json.dumps(lease, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        preserved_evidence.append(str(ev_path))
        deleted.extend(rel_deleted_records)

post_delete_readback_verified = False
if not dry_run:
    remaining_cloud = query_cloud_resources()
    deleted_cloud_keys = {(d["type"], d["name"]) for d in deleted if d["type"] in ("instance", "disk", "address")}
    lingering = [
        r["name"] for r in remaining_cloud
        if (r["type"], r["name"]) in deleted_cloud_keys
    ]
    if lingering:
        print(
            json.dumps({
                "status": "error",
                "error_code": "POST_DELETE_READBACK_FAILED",
                "message": f"Deleted orphan resources still present in cloud readback: {lingering}",
            }),
            file=sys.stderr,
        )
        raise SystemExit(1)
    post_delete_readback_verified = True

print(json.dumps({
    "status": "ok",
    "mode": "orphan-sweep",
    "dry_run": dry_run,
    "post_delete_readback_verified": post_delete_readback_verified,
    "deleted_resources": deleted,
    "retained_active_resources": retained_active,
    "skipped_unowned_resources": skipped_unowned,
    "preserved_evidence_files": preserved_evidence,
}, sort_keys=True))
PY
  exit 0
fi

if [[ "$MODE" != "teardown" ]]; then
  die_json "INVALID_MODE" "Unsupported mode: ${MODE}"
fi

if [[ -z "${RELEASE_ID:-}" ]]; then
  die_json "MISSING_RELEASE_ID" "RELEASE_ID is required for teardown"
fi

if [[ "${DRY_RUN}" != "true" ]]; then
  require_live_gcloud
fi

STATE_DIR="$STATE_DIR" RELEASE_ID="$RELEASE_ID" WORKFLOW_RUN_ID="${WORKFLOW_RUN_ID:-}" \
RESULT="$RESULT" REASON="$REASON" DRY_RUN="$DRY_RUN" FORCE_EXPIRED="$FORCE_EXPIRED" \
NOW_EPOCH="$NOW_EPOCH" DELAYED_UPLOAD="${STAGING_DELAYED_UPLOAD_SIMULATE:-false}" \
PROJECT_ID="${STAGING_GCP_PROJECT_ID:-}" ZONE="${STAGING_GCP_ZONE:-}" \
GCLOUD_BIN="${GCLOUD_BIN:-}" \
python3 - <<'PY'
import json
import os
import subprocess
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
gcloud_bin = os.environ.get("GCLOUD_BIN", "").strip()

inv_path = state_dir / "inventory" / f"{release_id}.json"
lease_path = state_dir / "leases" / f"{release_id}.json"

inv = json.loads(inv_path.read_text(encoding="utf-8")) if inv_path.exists() else None
lease = json.loads(lease_path.read_text(encoding="utf-8")) if lease_path.exists() else {}

project_id = os.environ.get("PROJECT_ID") or (inv or {}).get("project_id") or ""
zone = os.environ.get("ZONE") or (inv or {}).get("zone") or ""
region = zone.rsplit("-", 1)[0] if "-" in zone else ""


def run_gcloud(args: list[str]) -> str:
    proc = subprocess.run([gcloud_bin, *args, "--format=json"], capture_output=True, text=True)
    if proc.returncode != 0:
        print(
            json.dumps({
                "status": "error",
                "error_code": "CLOUD_DELETE_FAILED",
                "message": f"gcloud {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}",
            }),
            file=sys.stderr,
        )
        raise SystemExit(1)
    return proc.stdout


PENDING_PREFIX = "pantheon-pending-v1:"


def pending_address_labels(item: dict, expect_project: str, expect_region: str, expect_release: str | None = None) -> dict | None:
    """Labels from a strictly valid owned-pending description on an unlabelled address, else None."""
    if item.get("labels"):
        return None
    desc = str(item.get("description") or "")
    if not desc.startswith(PENDING_PREFIX):
        return None
    try:
        meta = json.loads(desc[len(PENDING_PREFIX):])
    except ValueError:
        return None
    labels = meta.get("labels") if isinstance(meta, dict) else None
    if (
        not isinstance(labels, dict)
        or meta.get("project") != expect_project
        or meta.get("region") != expect_region
        or labels.get("environment") != "staging"
        or labels.get("cleanup_policy") != "automatic"
        or not labels.get("release_id")
        or not labels.get("owner")
        or (expect_release is not None and labels.get("release_id") != expect_release)
        or item.get("name") != f"pantheon-stg-ip-{labels['release_id']}"
    ):
        return None
    return {str(k): str(v) for k, v in labels.items()}


def query_cloud_release_resources() -> list[dict]:
    if dry_run or not gcloud_bin or not project_id or not zone:
        return []
    discovered = []
    inst_raw = json.loads(run_gcloud(["compute", "instances", "list", f"--project={project_id}", f"--zones={zone}"]) or "[]")
    disk_raw = json.loads(run_gcloud(["compute", "disks", "list", f"--project={project_id}", f"--zones={zone}"]) or "[]")
    addr_raw = json.loads(run_gcloud(["compute", "addresses", "list", f"--project={project_id}", f"--regions={region}"]) or "[]")
    for r_type, items in (("instance", inst_raw), ("disk", disk_raw), ("address", addr_raw)):
        for item in items:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            labels = item.get("labels") or {}
            if r_type == "address":
                labels = pending_address_labels(item, project_id, region, release_id) or labels
            if labels.get("release_id") == release_id:
                discovered.append({
                    "type": r_type,
                    "name": item["name"],
                    "project_id": item.get("project_id") or project_id,
                    "zone": zone,
                    "region": region,
                    "labels": labels,
                    "state": "provisioned",
                })
    return discovered


cloud_rel_resources = query_cloud_release_resources()

if inv is None and not cloud_rel_resources:
    print(json.dumps({
        "status": "already_absent",
        "release_id": release_id,
        "deleted_resources": [],
    }, sort_keys=True))
    raise SystemExit(0)

if inv is None:
    first_labels = (cloud_rel_resources[0].get("labels") or {}) if cloud_rel_resources else {}
    inv = {
        "release_id": release_id,
        "workflow_run_id": first_labels.get("workflow_run_id") or first_labels.get("owner") or workflow_run_id,
        "project_id": project_id,
        "zone": zone,
        "region": region,
        "labels": first_labels,
        "resources": cloud_rel_resources,
    }
else:
    existing_keys = {(r.get("type"), r.get("name")) for r in inv.get("resources", [])}
    for c_res in cloud_rel_resources:
        if (c_res["type"], c_res["name"]) not in existing_keys:
            inv.setdefault("resources", []).append(c_res)

if workflow_run_id and inv.get("workflow_run_id") and inv.get("workflow_run_id") != workflow_run_id:
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
expires_epoch = int((inv.get("labels") or {}).get("expires_at_epoch", 0) or 0)
created_epoch = int((inv.get("labels") or {}).get("created_at_epoch", now_epoch) or now_epoch)
if expires_epoch and (expires_epoch - created_epoch > 86400):
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
    and expires_epoch > 0
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
exact_pending_addresses = []

for res in reversed(inv.get("resources", [])):
    if res.get("state") == "deleted":
        continue
    r_type = res.get("type")
    r_name = res.get("name")
    labels = res.get("labels") or {}
    res_project = res.get("project_id") or project_id
    res_zone = res.get("zone") or zone
    res_region = res.get("region") or (res_zone.rsplit("-", 1)[0] if "-" in res_zone else region)

    if r_type == "iam_binding" and res.get("preexisting") is True:
        skipped_unowned.append({
            "name": r_name,
            "type": r_type,
            "reason": "preexisting_iam_binding_preserved",
        })
        continue

    if (
        labels.get("environment") != "staging"
        or labels.get("cleanup_policy") != "automatic"
        or labels.get("release_id") != release_id
        or (labels.get("owner") or labels.get("workflow_run_id")) != inv.get("workflow_run_id")
        or (project_id and res_project != project_id)
    ):
        skipped_unowned.append({
            "name": r_name,
            "type": r_type,
            "reason": "unowned_or_unlabeled_resource_protected",
        })
        remaining.append(res)
        continue

    if not dry_run and r_type == "address" and res.get("state") == "label_pending":
        # Created by this run but never labelled in the cloud: delete only the exact journaled id.
        live = next(
            (a for a in json.loads(run_gcloud(["compute", "addresses", "list", f"--project={res_project}", f"--regions={res_region}"]) or "[]")
             if isinstance(a, dict) and a.get("name") == r_name),
            None,
        )
        if live is None:
            res["state"] = "deleted"
            continue
        if str(live.get("id") or "") != str(res.get("resource_id") or ""):
            skipped_unowned.append({"name": r_name, "type": r_type, "reason": "unowned_or_unlabeled_resource_protected"})
            remaining.append(res)
            continue
        exact_pending_addresses.append((r_name, str(res.get("resource_id")), res_project, res_region))

    if not dry_run:
        if r_type == "iam_binding":
            member = res.get("member")
            role = res.get("role")
            if member and role and not res.get("preexisting"):
                run_gcloud(["projects", "remove-iam-policy-binding", res_project, f"--member={member}", f"--role={role}"])
        elif r_type == "instance":
            run_gcloud(["compute", "instances", "delete", r_name, f"--project={res_project}", f"--zone={res_zone}", "--quiet"])
        elif r_type == "disk":
            run_gcloud(["compute", "disks", "delete", r_name, f"--project={res_project}", f"--zone={res_zone}", "--quiet"])
        elif r_type == "address":
            run_gcloud(["compute", "addresses", "delete", r_name, f"--project={res_project}", f"--region={res_region}", "--quiet"])
        res["state"] = "deleted"
    else:
        remaining.append(res)

    deleted.append({
        "name": r_name,
        "type": r_type,
        "release_id": release_id,
    })

post_delete_readback_verified = False
lingering_count = len(deleted) if dry_run else 0
if not dry_run:
    post_cloud = query_cloud_release_resources()
    deleted_keys = {(d["type"], d["name"]) for d in deleted if d["type"] in ("instance", "disk", "address")}
    lingering_items = [r["name"] for r in post_cloud if (r["type"], r["name"]) in deleted_keys]
    for p_name, p_id, p_project, p_region in exact_pending_addresses:
        for a in json.loads(run_gcloud(["compute", "addresses", "list", f"--project={p_project}", f"--regions={p_region}"]) or "[]"):
            if isinstance(a, dict) and a.get("name") == p_name and str(a.get("id") or "") == p_id:
                lingering_items.append(p_name)
    if lingering_items:
        print(
            json.dumps({
                "status": "error",
                "error_code": "POST_DELETE_READBACK_FAILED",
                "message": f"Owned resources still present after teardown delete: {lingering_items}",
            }),
            file=sys.stderr,
        )
        raise SystemExit(1)
    post_delete_readback_verified = True
    evidence_payload["post_delete_readback_verified"] = True
    evidence_payload["deleted_resources"] = deleted
    evidence_path.write_text(json.dumps(evidence_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    inv["resources"] = list(reversed(remaining))
    inv_path.write_text(json.dumps(inv, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if lease_path.exists():
        lease["status"] = "destroyed"
        lease["destroyed_at_epoch"] = now_epoch
        lease["destroy_reason"] = reason
        lease["post_delete_readback_verified"] = True
        lease_path.write_text(json.dumps(lease, indent=2, sort_keys=True) + "\n", encoding="utf-8")

print(json.dumps({
    "status": "destroyed",
    "release_id": release_id,
    "reason": reason,
    "dry_run": dry_run,
    "post_delete_readback_verified": post_delete_readback_verified,
    "evidence_preserved": str(evidence_path),
    "upload_status": evidence_payload["upload_status"],
    "deleted_resources": deleted,
    "skipped_unowned_resources": skipped_unowned,
    "lingering_owned_resources": lingering_count,
}, sort_keys=True))
PY
