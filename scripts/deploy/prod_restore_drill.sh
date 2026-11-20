#!/usr/bin/env bash
# Production backup/restore drill: measures RPO and RTO from the newest auto-snapshot of the data disk.
# Read-only towards live data: the scratch disk is attached READ-ONLY and mounted read-only; postgres and the owner
# stores are copied to a temp dir on the boot disk and a throwaway postgres (digest-pinned, no published ports, own
# network, unique name) runs on the copy. Nothing is ever written under PROD_DATA_MOUNT.
# Readability proof = runbook section 4: catalog hash (information_schema.columns, user schemas) + per-table row counts
# of the restored DB, compared with the live DB read at drill end (closest available to "snapshot time": a catalog hash
# difference means a migration landed after the snapshot and FAILS; row-count drift is reported, since live churns),
# plus the sha256 file manifest of exactly bff runtime governance product from the restored disk vs the copy.
# A trap always tears down (container, network, temp copy, unmount, detach, delete scratch disk) and reads back.
# RPO_s = drill start - snapshot creation; RTO_s = drill start -> readability proven.
# Exit: 0 ok, 1 refused, 2 RPO/RTO over budget, 3 drill stage failed. One JSON line on stdout, no secrets.
# Env: PROD_GCP_PROJECT_ID PROD_GCP_ZONE PROD_DATA_DISK PROD_VM_NAME PROD_DATA_MOUNT PROD_RPO_SECONDS PROD_RTO_SECONDS
#      PROD_POSTGRES_USER PROD_POSTGRES_DB PANTHEON_POSTGRES_IMAGE [PROD_DRILL_MNT_BASE=/mnt PROD_DRILL_TMP_BASE=/var/tmp PROD_DRILL_POLL_SECONDS=2]
set -uo pipefail
GCLOUD="${GCLOUD_BIN:-gcloud}"; DOCKER="${DOCKER_BIN:-docker}"
priv() { if [[ $EUID -eq 0 ]]; then "$@"; else sudo "$@"; fi; }
fail() { printf '{"status":"refused","error_code":"%s","message":"%s"}\n' "$1" "$2" >&2; exit 1; }
for v in PROD_GCP_PROJECT_ID PROD_GCP_ZONE PROD_DATA_DISK PROD_VM_NAME PROD_DATA_MOUNT PROD_RPO_SECONDS PROD_RTO_SECONDS \
         PROD_POSTGRES_USER PROD_POSTGRES_DB PANTHEON_POSTGRES_IMAGE; do
  [[ -n "${!v:-}" ]] || fail MISSING_INPUT "required input ${v} is missing"
done
[[ "$PANTHEON_POSTGRES_IMAGE" == *@sha256:* ]] || fail IMAGE_NOT_PINNED "postgres image must be digest-pinned"
PROJECT="${CLOUDSDK_CORE_PROJECT:-$("$GCLOUD" config get-value project 2>/dev/null)}"
[[ "$PROJECT" == "$PROD_GCP_PROJECT_ID" ]] || fail WRONG_PROJECT "active project does not equal PROD_GCP_PROJECT_ID"
ZONE="$PROD_GCP_ZONE"; DISK="$PROD_DATA_DISK"; VM="$PROD_VM_NAME"
"$GCLOUD" compute disks describe "$DISK" --project "$PROJECT" --zone "$ZONE" --format='value(name)' >/dev/null 2>&1 \
  || fail DISK_NOT_FOUND "data disk not found in PROD_GCP_ZONE"

T0=$(date +%s); UTC="$(date -u +%Y%m%dt%H%M%Sz)"
SCRATCH="pantheon-prod-restore-drill-${UTC}"; DEV="restore-drill-${UTC}"
MNT="${PROD_DRILL_MNT_BASE:-/mnt}/restore-drill-${UTC}"; TMPD="${PROD_DRILL_TMP_BASE:-/var/tmp}/pantheon-restore-drill-${UTC}"
CTR="pantheon-restore-drill-${UTC}"; NET="$CTR"
CHECKS='{}'; STAGE=init; ATTACHED=0; CREATED=0; MOUNTED=0; CTR_UP=0; NET_UP=0; TMP_UP=0; STATUS=failed
G="$GCLOUD"; PZ=(--project "$PROJECT" --zone "$ZONE")
OWNER_DIRS=(bff runtime governance product)

cleanup() {
  local rc=$?; trap - EXIT
  [[ $CTR_UP -eq 1 ]] && { "$DOCKER" stop "$CTR" >/dev/null 2>&1; "$DOCKER" rm -f "$CTR" >/dev/null 2>&1; }
  [[ $NET_UP -eq 1 ]] && "$DOCKER" network rm "$NET" >/dev/null 2>&1
  [[ $TMP_UP -eq 1 ]] && priv rm -rf -- "$TMPD" >/dev/null 2>&1
  [[ $MOUNTED -eq 1 ]] && { priv umount "$MNT" >/dev/null 2>&1; priv rmdir "$MNT" >/dev/null 2>&1; }
  [[ $ATTACHED -eq 1 ]] && "$G" compute instances detach-disk "$VM" "${PZ[@]}" --disk "$SCRATCH" >/dev/null 2>&1
  [[ $CREATED -eq 1 ]] && "$G" compute disks delete "$SCRATCH" "${PZ[@]}" --quiet >/dev/null 2>&1
  local rb_ctr rb_net rb_tmp rb_mnt rb_disk
  rb_ctr=$("$DOCKER" ps -aq --filter "name=^${CTR}\$" 2>/dev/null | wc -l)
  rb_net=$("$DOCKER" network ls -q --filter "name=^${NET}\$" 2>/dev/null | wc -l)
  rb_tmp=0; [[ -e "$TMPD" ]] && rb_tmp=1
  rb_mnt=0; [[ -e "$MNT" ]] && rb_mnt=1
  rb_disk=$("$G" compute disks list --project "$PROJECT" --filter="name=${SCRATCH}" --format='value(name)' 2>/dev/null | wc -l)
  [[ $((rb_ctr+rb_net+rb_tmp+rb_mnt+rb_disk)) -eq 0 ]] && CLEAN=true || CLEAN=false
  [[ $CLEAN == true ]] || { STATUS=failed; rc=3; }
  python3 -I - "$STATUS" "${SNAP:-}" "${RPO:-}" "${RTO:-}" "$STAGE" "$CHECKS" "$CLEAN" "$rb_ctr" "$rb_net" "$rb_tmp" "$rb_mnt" "$rb_disk" <<'PY'
import json, sys
st, snap, rpo, rto, stage, checks, clean, *rb = sys.argv[1:]
names = ["container", "network", "temp_copy", "mount", "scratch_disk"]
print(json.dumps({"status": st, "snapshot": snap or None, "rpo_seconds": int(rpo) if rpo else None,
  "rto_seconds": int(rto) if rto else None, "failed_stage": None if st == "ok" else stage,
  "checks": json.loads(checks), "cleanup": {"clean": clean == "true", **{n: int(v) for n, v in zip(names, rb)}}},
  sort_keys=True))
PY
  exit "$rc"
}
trap cleanup EXIT
die() { STAGE="$1"; STATUS=failed; exit 3; }

STAGE=select_snapshot
SNAP_LINE="$("$G" compute snapshots list --project "$PROJECT" --filter="sourceDisk~/disks/${DISK}\$ AND autoCreated=true" \
  --sort-by=~creationTimestamp --limit=1 --format='value(name,creationTimestamp)' 2>/dev/null)" || die select_snapshot
[[ -n "$SNAP_LINE" ]] || die select_snapshot
SNAP="${SNAP_LINE%%[[:space:]]*}"; SNAP_TS="${SNAP_LINE##*[[:space:]]}"
RPO="$(python3 -I -c 'import datetime,sys;print(int(datetime.datetime.now(datetime.timezone.utc).timestamp()-datetime.datetime.fromisoformat(sys.argv[1]).timestamp()))' "$SNAP_TS")" || die select_snapshot
RPO=$(( RPO - ($(date +%s) - T0) ))   # RPO is measured at drill start

STAGE=create_disk
"$G" compute disks create "$SCRATCH" "${PZ[@]}" --source-snapshot "$SNAP" --labels environment=production,managed_by=pantheon,purpose=restore-drill >/dev/null 2>&1 \
  || { "$G" compute disks describe "$SCRATCH" "${PZ[@]}" >/dev/null 2>&1 && CREATED=1; die create_disk; }
CREATED=1
STAGE=attach_readonly
"$G" compute instances attach-disk "$VM" "${PZ[@]}" --disk "$SCRATCH" --mode ro --device-name "$DEV" >/dev/null 2>&1 \
  || { ATTACHED=1; die attach_readonly; }
ATTACHED=1
STAGE=mount_readonly
priv mkdir -p "$MNT" && priv mount -o ro,noload "/dev/disk/by-id/google-${DEV}" "$MNT" >/dev/null 2>&1 || { MOUNTED=1; die mount_readonly; }
MOUNTED=1

STAGE=copy
priv mkdir -p "$TMPD" || die copy; TMP_UP=1
for d in postgres "${OWNER_DIRS[@]}"; do
  [[ -d "$MNT/$d" ]] || die copy
  priv cp -a -- "$MNT/$d" "$TMPD/$d" || die copy
done

STAGE=start_postgres
"$DOCKER" network create --internal --label purpose=restore-drill "$NET" >/dev/null 2>&1 || { NET_UP=1; die start_postgres; }
NET_UP=1
"$DOCKER" run -d --name "$CTR" --network "$NET" --label purpose=restore-drill -e "POSTGRES_USER=${PROD_POSTGRES_USER}" \
  -e "POSTGRES_DB=${PROD_POSTGRES_DB}" -v "${TMPD}/postgres:/var/lib/postgresql/data" "$PANTHEON_POSTGRES_IMAGE" >/dev/null 2>&1 \
  || { CTR_UP=1; die start_postgres; }
CTR_UP=1
ready=0; for _ in $(seq 1 60); do
  "$DOCKER" exec "$CTR" pg_isready -q -U "$PROD_POSTGRES_USER" -d "$PROD_POSTGRES_DB" >/dev/null 2>&1 && { ready=1; break; }; sleep "${PROD_DRILL_POLL_SECONDS:-2}"
done
[[ $ready -eq 1 ]] || die start_postgres

STAGE=verify
CATALOG_SQL="select table_schema||'.'||table_name||'.'||column_name||':'||data_type from information_schema.columns where table_schema not in ('pg_catalog','information_schema') order by 1"
TABLES_SQL="select table_schema||'.'||table_name from information_schema.tables where table_type='BASE TABLE' and table_schema not in ('pg_catalog','information_schema') order by 1"
pq() { "$DOCKER" exec -i "$1" sh -c 'psql -X -v ON_ERROR_STOP=1 -At -U "$POSTGRES_USER" -d "$POSTGRES_DB"' 2>/dev/null; }
counts() { # $1 container -> "schema.table=count" lines
  local t; while IFS= read -r t; do printf '%s=%s\n' "$t" "$(pq "$1" <<<"select count(*) from ${t%%.*}.\"${t#*.}\"")" || return 1; done < <(pq "$1" <<<"$TABLES_SQL")
}
LIVE="$("$DOCKER" ps -q --filter label=com.docker.compose.service=postgres | head -1)"
[[ -n "$LIVE" ]] || die verify
R_CAT="$(pq "$CTR" <<<"$CATALOG_SQL" | sha256sum | cut -d' ' -f1)"; L_CAT="$(pq "$LIVE" <<<"$CATALOG_SQL" | sha256sum | cut -d' ' -f1)"
R_CNT="$(counts "$CTR")" || die verify; L_CNT="$(counts "$LIVE")" || die verify
mani() { (cd "$1" && for d in "${OWNER_DIRS[@]}"; do [[ -d $d ]] && find "$d" -type f -print0; done | sort -z | xargs -0 -r sha256sum | sha256sum | cut -d' ' -f1); }
M_SRC="$(mani "$MNT")"; M_COPY="$(mani "$TMPD")"
RTO=$(( $(date +%s) - T0 ))
CHECKS="$(python3 -I - "$R_CAT" "$L_CAT" "$R_CNT" "$L_CNT" "$M_SRC" "$M_COPY" <<'PY'
import json, sys
rc, lc, rcnt, lcnt, ms, mc = sys.argv[1:]
parse = lambda s: dict(l.rsplit("=", 1) for l in s.splitlines() if l)
r, l = parse(rcnt), parse(lcnt)
drift = {t: int(l[t]) - int(r[t]) for t in l if t in r and l[t] != r[t]}
print(json.dumps({"catalog_hash_equal": rc == lc, "catalog_sha256": "sha256:" + rc, "tables_restored": len(r),
  "tables_match_live": sorted(r) == sorted(l), "row_count_drift_vs_live": drift,
  "owner_store_manifest_equal": ms == mc, "owner_store_manifest_sha256": "sha256:" + ms}, sort_keys=True))
PY
)" || die verify
python3 -I -c 'import json,sys;c=json.loads(sys.argv[1]);sys.exit(0 if c["catalog_hash_equal"] and c["tables_match_live"] and c["owner_store_manifest_equal"] and c["tables_restored"]>0 else 1)' "$CHECKS" || die verify
STATUS=ok; STAGE=done; code=0
if (( RPO > PROD_RPO_SECONDS )) || (( RTO > PROD_RTO_SECONDS )); then STATUS=over_budget; STAGE=budget; code=2; fi
exit "$code"
