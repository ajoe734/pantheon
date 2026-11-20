#!/usr/bin/env bash
# Production data-disk snapshot schedule (RPO control). Idempotent; never deletes a snapshot,
# never touches the boot disk or any disk other than PROD_DATA_DISK.
#   ensure : create resource policy pantheon-prod-data-hourly (hourly, 3-day retention, keep auto snapshots on
#            source-disk delete, storage location = disk region, labels) if absent, then attach it to the data disk.
#   check  : read-only; one JSON line {policy_present, attached, schedule, retention_days, newest_snapshot,
#            newest_age_seconds, max_age_seconds}. Exit 1 when the newest auto-snapshot is older than
#            PROD_RPO_SECONDS + PROD_SNAPSHOT_GRACE_SECONDS (default 900) or absent.
# Env: PROD_GCP_PROJECT_ID PROD_GCP_ZONE PROD_DATA_DISK PROD_RPO_SECONDS [PROD_SNAPSHOT_GRACE_SECONDS] [GCLOUD_BIN]
set -uo pipefail
GCLOUD="${GCLOUD_BIN:-gcloud}"
POLICY="pantheon-prod-data-hourly"
fail() { printf '{"status":"refused","error_code":"%s","message":"%s"}\n' "$1" "$2" >&2; exit "${3:-1}"; }
MODE="${1:-}"; [[ "$MODE" == ensure || "$MODE" == check ]] || fail USAGE "usage: prod_snapshot_schedule.sh ensure|check"
for v in PROD_GCP_PROJECT_ID PROD_GCP_ZONE PROD_DATA_DISK PROD_RPO_SECONDS; do
  [[ -n "${!v:-}" ]] || fail MISSING_INPUT "required input ${v} is missing"
done
[[ "$PROD_RPO_SECONDS" =~ ^[0-9]+$ ]] || fail MISSING_INPUT "PROD_RPO_SECONDS must be a positive integer"
GRACE="${PROD_SNAPSHOT_GRACE_SECONDS:-900}"
[[ "$GRACE" =~ ^[0-9]+$ ]] || fail MISSING_INPUT "PROD_SNAPSHOT_GRACE_SECONDS must be an integer"
PROJECT="${CLOUDSDK_CORE_PROJECT:-$("$GCLOUD" config get-value project 2>/dev/null)}"
[[ "$PROJECT" == "$PROD_GCP_PROJECT_ID" ]] || fail WRONG_PROJECT "active project does not equal PROD_GCP_PROJECT_ID"
DISK="$PROD_DATA_DISK"; ZONE="$PROD_GCP_ZONE"; REGION="${ZONE%-*}"
"$GCLOUD" compute disks describe "$DISK" --project "$PROJECT" --zone "$ZONE" --format='value(name)' >/dev/null 2>&1 \
  || fail DISK_NOT_FOUND "data disk not found in PROD_GCP_ZONE"

policy_json() { "$GCLOUD" compute resource-policies describe "$POLICY" --project "$PROJECT" --region "$REGION" --format=json 2>/dev/null; }
disk_policies() { "$GCLOUD" compute disks describe "$DISK" --project "$PROJECT" --zone "$ZONE" --format='value(resourcePolicies)' 2>/dev/null; }

if [[ "$MODE" == ensure ]]; then
  if ! policy_json >/dev/null; then
    "$GCLOUD" compute resource-policies create snapshot-schedule "$POLICY" --project "$PROJECT" --region "$REGION" \
      --hourly-schedule=1 --start-time=00:00 --max-retention-days=3 --on-source-disk-delete=keep-auto-snapshots \
      --storage-location "$REGION" --labels environment=production,managed_by=pantheon >/dev/null \
      || fail POLICY_CREATE_FAILED "resource policy create failed"
  fi
  if [[ "$(disk_policies)" != *"/resourcePolicies/${POLICY}"* ]]; then
    "$GCLOUD" compute disks add-resource-policies "$DISK" --project "$PROJECT" --zone "$ZONE" \
      --resource-policies "$POLICY" >/dev/null || fail POLICY_ATTACH_FAILED "resource policy attach failed"
  fi
  MODE=check
fi

pol="$(policy_json || true)"
att="false"; [[ "$(disk_policies)" == *"/resourcePolicies/${POLICY}"* ]] && att="true"
newest="$("$GCLOUD" compute snapshots list --project "$PROJECT" \
  --filter="sourceDisk~/disks/${DISK}\$ AND autoCreated=true" --sort-by=~creationTimestamp --limit=1 \
  --format='value(name,creationTimestamp)' 2>/dev/null || true)"
python3 -I - "$pol" "$att" "$newest" "$PROD_RPO_SECONDS" "$GRACE" <<'PY'
import datetime, json, sys
pol, att, newest, rpo, grace = sys.argv[1:6]
p = json.loads(pol) if pol.strip() else {}
sch = (p.get("snapshotSchedulePolicy") or {})
hourly = (sch.get("schedule") or {}).get("hourlySchedule") or {}
ret = (sch.get("retentionPolicy") or {}).get("maxRetentionDays")
name, age = None, None
if newest.strip():
    name, ts = newest.split()[:2]
    t = datetime.datetime.fromisoformat(ts)
    age = int((datetime.datetime.now(datetime.timezone.utc) - t).total_seconds())
limit = int(rpo) + int(grace)
out = {"policy_present": bool(p), "attached": att == "true",
       "schedule": f"hourly/{hourly.get('hoursInCycle')}h" if hourly else None, "retention_days": ret,
       "newest_snapshot": name, "newest_age_seconds": age, "max_age_seconds": limit}
print(json.dumps(out, sort_keys=True))
sys.exit(0 if out["policy_present"] and out["attached"] and age is not None and age <= limit else 1)
PY
