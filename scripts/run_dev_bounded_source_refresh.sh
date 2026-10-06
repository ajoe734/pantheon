#!/usr/bin/env bash
# Run the bounded Taiwan official market refresh once per trading day on Pantheon dev.
# Outside the bounded run, source-ingest controller stays reconcile_only and egress returns to deny.
set -euo pipefail

WORKTREE_DIR="."; FORCE=false; OUTPUT_FILE=""; TIMEOUT_SECONDS=300
ALLOWLIST_HOSTS="openapi.twse.com.tw,www.twse.com.tw,www.tpex.org.tw"
CONNECTOR_ID="tw-twse-tpex-official-market"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --worktree-dir) WORKTREE_DIR="$2"; shift 2 ;;
    --force) FORCE=true; shift ;;
    --output|--output-file) OUTPUT_FILE="$2"; shift 2 ;;
    --timeout-seconds) TIMEOUT_SECONDS="$2"; shift 2 ;;
    *) echo "[tw-refresh] unknown option: $1" >&2; exit 2 ;;
  esac
done

cd "$WORKTREE_DIR"

write_result() {
  [[ -n "$OUTPUT_FILE" ]] && { mkdir -p "$(dirname "$OUTPUT_FILE")"; printf '%s\n' "$1" > "$OUTPUT_FILE"; }
  printf '%s\n' "$1"
}

# 1. Calendar & Idempotency Pre-flight Check
preflight_output="$(python3 - "${FORCE}" "${CONNECTOR_ID}" <<'PY'
import json, sys, urllib.request
from datetime import datetime, time, timedelta, timezone

TAIPEI_TZ = timezone(timedelta(hours=8))
force, now_utc = sys.argv[1].lower() == "true", datetime.now(timezone.utc)
now_taipei = now_utc.astimezone(TAIPEI_TZ)
taipei_date_str = str(now_taipei.date())

if not force and now_taipei.weekday() >= 5:
    sys.exit(print(json.dumps({"status": "skipped", "reason": "weekend", "taipei_date": taipei_date_str, "checked_at": now_utc.isoformat()})))
if not force and now_taipei.time() < time(13, 30):
    sys.exit(print(json.dumps({"status": "skipped", "reason": "session_not_closed", "taipei_date": taipei_date_str, "checked_at": now_utc.isoformat()})))

try:
    from services.execution.market_snapshot_admission import evaluate_taiwan_market_freshness, validate_taiwan_calendar_evidence
    req = urllib.request.Request("http://127.0.0.1:18097/api/source-ingest/snapshots/latest?symbol=0050.TW", headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=3) as resp:
        snap = json.loads(resp.read().decode())
    cal_ev = snap.get("calendar_evidence") or (snap.get("lineage") or {}).get("calendar_evidence")
    if cal_ev is not None:
        c_ok, c_err, c_norm = validate_taiwan_calendar_evidence(cal_ev, now_dt=now_utc)
        if not c_ok:
            sys.exit(print(json.dumps({"status": "error", "reason": "market_input_calendar_unverifiable", "detail": c_err, "taipei_date": taipei_date_str})))
        if taipei_date_str in (c_norm.get("holidays") or {}):
            sys.exit(print(json.dumps({"status": "skipped", "reason": "holiday", "taipei_date": taipei_date_str, "checked_at": now_utc.isoformat()})))
    ev_dt, obs_dt = datetime.fromisoformat(snap["event_time"].replace("Z", "+00:00")), datetime.fromisoformat(snap["observed_at"].replace("Z", "+00:00"))
    if not force and ev_dt.astimezone(TAIPEI_TZ).date() == now_taipei.date() and obs_dt >= datetime(now_taipei.year, now_taipei.month, now_taipei.day, 13, 30, tzinfo=TAIPEI_TZ).astimezone(timezone.utc):
        ok, _, _ = evaluate_taiwan_market_freshness(event_time_dt=ev_dt, now_dt=now_utc, refresh_receipt_dt=obs_dt, lineage=snap.get("lineage") or {}, max_refresh_age_seconds=86400, calendar_evidence=cal_ev)
        if ok:
            sys.exit(print(json.dumps({"status": "noop", "reason": "already_fresh", "taipei_date": taipei_date_str, "snapshot_id": snap.get("snapshot_id"), "checked_at": now_utc.isoformat()})))
except SystemExit:
    raise
except Exception:
    pass

print(json.dumps({"status": "proceed", "taipei_date": taipei_date_str}))
PY
)"

preflight_status="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("status") or "")' "${preflight_output}")"
case "$preflight_status" in
  skipped|noop) echo "[tw-refresh] preflight check: ${preflight_output}" >&2; write_result "${preflight_output}"; exit 0 ;;
  error) echo "[tw-refresh] preflight check failed: ${preflight_output}" >&2; write_result "${preflight_output}"; exit 1 ;;
esac

echo "[tw-refresh] proceeding with bounded Taiwan market refresh..." >&2

# 2. Cleanup Trap: Egress returns to deny, controller returns to reconcile_only
restore_egress_and_controller() {
  local exit_code=$?
  trap - EXIT INT TERM
  echo "[tw-refresh] restoring egress to deny and controller to reconcile_only..." >&2
  PANTHEON_EXTERNAL_EGRESS=deny PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS="" \
  SOURCE_INGEST_CONTROLLER_MODE=reconcile_only SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL=scheduled_tick \
  SOURCE_INGEST_CONTROLLER_MAX_TICKS=0 SOURCE_INGEST_CONTROLLER_RESTART_POLICY=unless-stopped \
    docker compose -p pantheon -f docker-compose.yml up -d --no-deps source-ingest >/dev/null 2>&1 || true
  COMPOSE_PROFILES="source-ingest-scheduler,workers" \
    docker compose -p pantheon -f docker-compose.yml rm -f -s \
      source-ingest-scheduler source-ingest-agora-projector >/dev/null 2>&1 || true
  exit "$exit_code"
}
trap restore_egress_and_controller EXIT INT TERM

# 3. Resolve active paper symbols
active_symbols="$(python3 -c '
import json, os, re, pathlib
p = pathlib.Path(os.environ.get("PANTHEON_RUNTIME_BINDING_STORE_PATH", "/data/runtime/runtime_bindings.json"))
syms = []
if p.exists():
    try:
        for b in json.loads(p.read_text()):
            s = str(b.get("symbol") or (b.get("metadata") or {}).get("symbol") or "").strip().upper()
            if s and re.fullmatch(r"[A-Z0-9_-]+\.(?:TW|TWSE|TWO|TPEX)", s) and s not in syms:
                syms.append(s)
    except Exception: pass
print(",".join(syms or ["0050.TW"]))
')"

# 4. Start source-ingest with bounded allowlist egress
PANTHEON_EXTERNAL_EGRESS=allowlist PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS="${ALLOWLIST_HOSTS}" \
SOURCE_INGEST_CONTROLLER_MODE=reconcile_and_pull \
  docker compose -p pantheon -f docker-compose.yml up -d --no-deps source-ingest

for _ in $(seq 1 30); do curl -fsS http://127.0.0.1:18097/readyz >/dev/null 2>&1 && break; sleep 1; done

wait_container() {
  local svc="$1" start; start="$(date +%s)"
  while (( $(date +%s) - start < TIMEOUT_SECONDS )); do
    local cid; cid="$(docker compose -p pantheon -f docker-compose.yml ps -a -q "$svc" 2>/dev/null || true)"
    if [[ -n "$cid" && "$(docker inspect --format '{{.State.Status}}' "$cid" 2>/dev/null || true)" == "exited" ]]; then
      local ec; ec="$(docker inspect --format '{{.State.ExitCode}}' "$cid")"
      [[ "$ec" == "0" ]] || { echo "[tw-refresh] $svc failed with exit code $ec" >&2; exit 1; }
      return 0
    fi
    sleep 2
  done
  echo "[tw-refresh] $svc timed out after ${TIMEOUT_SECONDS}s" >&2; exit 1
}

# 5. Execute bounded source-ingest-scheduler (finite 1 tick)
refresh_started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
COMPOSE_PROFILES="source-ingest-scheduler,workers" SOURCE_INGEST_CONTROLLER_MODE=reconcile_and_pull \
SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL=reconciled_live_proof SOURCE_INGEST_CONTROLLER_MAX_TICKS=1 \
SOURCE_INGEST_CONTROLLER_RESTART_POLICY=no SOURCE_INGEST_CONTROLLER_FORCE_CONNECTOR_IDS="${CONNECTOR_ID}" \
SOURCE_INGEST_CONTROLLER_EXCLUSIVE_CONNECTOR_IDS="${CONNECTOR_ID}" SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY=1 \
SOURCE_INGEST_MAX_RECORDS=100 SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS="${active_symbols}" \
  docker compose -p pantheon -f docker-compose.yml up -d --no-deps source-ingest-scheduler

wait_container source-ingest-scheduler

# 6. Run Agora projector
COMPOSE_PROFILES="source-ingest-scheduler,workers" \
  docker compose -p pantheon -f docker-compose.yml up -d --no-deps source-ingest-agora-projector

wait_container source-ingest-agora-projector

# 7. Post-refresh readback verification
evidence_dir="$(mktemp -d)"
projector_id="$(docker compose -p pantheon -f docker-compose.yml ps -a -q source-ingest-agora-projector 2>/dev/null || true)"
curl -fsS --get --data-urlencode "connector_id=${CONNECTOR_ID}" http://127.0.0.1:18097/api/source-ingest/receipts -o "${evidence_dir}/receipts.json"
curl -fsS http://127.0.0.1:18097/api/source-ingest/controller/readback -o "${evidence_dir}/readback.json"
[[ -n "$projector_id" ]] && docker cp "${projector_id}:/data/bff/agora_watchlist.json" "${evidence_dir}/agora_watchlist.json" 2>/dev/null || true

verification_json="$(python3 - "${CONNECTOR_ID}" "${refresh_started_at}" "${evidence_dir}" "${active_symbols}" <<'PY'
import json, sys, urllib.request
from datetime import datetime, timezone
from pathlib import Path
from services.execution.market_snapshot_admission import evaluate_taiwan_market_freshness

cid, started_raw, ev_dir, syms_csv = sys.argv[1:5]
started = datetime.fromisoformat(started_raw.replace("Z", "+00:00"))
rcpts = json.loads((Path(ev_dir) / "receipts.json").read_text()).get("receipts") or []
valid = [r for r in rcpts if r.get("connector_id") == cid and r.get("status") == "completed" and r.get("source_timestamp_status") == "valid" and datetime.fromisoformat((r.get("finished_at") or r.get("created_at")).replace("Z", "+00:00")) >= started]
if not valid:
    raise SystemExit("no new valid completed receipt produced by bounded refresh")
receipt = max(valid, key=lambda x: x.get("finished_at") or x.get("created_at"))

now_utc, admitted = datetime.now(timezone.utc), []
for s in [x for x in syms_csv.split(",") if x]:
    with urllib.request.urlopen(f"http://127.0.0.1:18097/api/source-ingest/snapshots/latest?symbol={s}", timeout=5) as resp:
        snap = json.loads(resp.read().decode())
    ev_dt, obs_dt = datetime.fromisoformat(snap["event_time"].replace("Z", "+00:00")), datetime.fromisoformat(snap["observed_at"].replace("Z", "+00:00"))
    lineage = snap.get("lineage") or {}
    ok, reason, detail = evaluate_taiwan_market_freshness(event_time_dt=ev_dt, now_dt=now_utc, refresh_receipt_dt=obs_dt, lineage=lineage, max_refresh_age_seconds=86400, calendar_evidence=snap.get("calendar_evidence") or lineage.get("calendar_evidence"))
    if not ok:
        raise SystemExit(f"refreshed snapshot failed admission for {s}: {reason} {detail}")
    admitted.append({"symbol": s, "snapshot_id": snap.get("snapshot_id")})

print(json.dumps({"status": "completed", "ingest_run_id": receipt["ingest_run_id"], "refreshed_at": now_utc.isoformat(), "admitted_symbols": admitted}, sort_keys=True))
PY
)"
rm -rf "${evidence_dir}"
write_result "${verification_json}"
