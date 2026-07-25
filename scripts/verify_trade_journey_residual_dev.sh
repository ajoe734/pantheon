#!/usr/bin/env bash
set -euo pipefail

cd "${PANTHEON_DEV_REPO:-/home/lupin/pantheon}"
compose=(docker compose -p pantheon -f docker-compose.yml)
service=operator-bff
key="tj-residual-smoke-$(date -u +%Y%m%dT%H%M%SZ)"

backend="$("${compose[@]}" exec -T "$service" sh -lc 'printf %s "$PANTHEON_TRADE_JOURNEY_ACTION_LEDGER_BACKEND"')"
drift="$("${compose[@]}" exec -T "$service" sh -lc 'printf %s "$PANTHEON_TRADE_JOURNEY_CLOCK_DRIFT_SECONDS"')"
test "$backend" = postgres
test "$drift" = 5

table="$("${compose[@]}" exec -T postgres psql -U pantheon_app -d pantheon -Atc "select to_regclass('public.trade_journey_action_ledger');")"
test "$table" = trade_journey_action_ledger

"${compose[@]}" exec -T -e SMOKE_KEY="$key" "$service" python - <<'PY'
import os
from concurrent.futures import ThreadPoolExecutor
from services.trade_journey.action_ledger import PostgresActionLedger

dsn = os.environ["PANTHEON_TRADE_JOURNEY_ACTION_LEDGER_DSN"]
key = os.environ["SMOKE_KEY"]
def reserve(_):
    return PostgresActionLedger(dsn).reserve(key, "smoke-hash")[0]
with ThreadPoolExecutor(max_workers=4) as pool:
    outcomes = list(pool.map(reserve, range(4)))
assert outcomes.count("new") == 1, outcomes
assert outcomes.count("pending") == 3, outcomes
PostgresActionLedger(dsn).complete(key, "smoke-hash", {"receipt_id": key, "status": "succeeded"})
print({"reservation_outcomes": outcomes})
PY

"${compose[@]}" restart "$service" >/dev/null
# The lifecycle projector can need slightly more than the ordinary 120-second
# restart budget to republish and accept live truth.  Permit one additional
# bounded 120-second window only while the exact deployment reports its known
# recovery state and continues converging, or while a recent exact/fresh
# observation remains bounded to the still-live exact BFF during dependency
# warm-up. Unexpected degradation, old/stale identity, a stalled non-zero
# backlog, or failure to accept live truth by the hard deadline still fails.
python3 scripts/wait_for_bff_lifecycle_readiness.py \
  --url http://127.0.0.1:18001/readyz \
  --expected-deployment-sha "$(git rev-parse HEAD)" \
  --initial-timeout-seconds 120 \
  --recovery-extension-seconds 120 \
  --stalled-timeout-seconds 45 \
  --poll-interval-seconds 2 \
  --request-timeout-seconds 2 \
  --exact-evidence-max-age-seconds 30

"${compose[@]}" exec -T -e SMOKE_KEY="$key" "$service" python - <<'PY'
import os
from datetime import datetime, timezone
from services.trade_journey.action_ledger import PostgresActionLedger
from services.trade_journey.materializer import JourneyMaterializer
from services.trade_journey.slo_data_quality import compute_data_quality_metrics, evaluate_data_quality, load_slo_targets

ledger = PostgresActionLedger(os.environ["PANTHEON_TRADE_JOURNEY_ACTION_LEDGER_DSN"])
state, receipt = ledger.reserve(os.environ["SMOKE_KEY"], "smoke-hash")
assert state == "replay" and receipt["receipt_id"] == os.environ["SMOKE_KEY"], (state, receipt)

events = [
 {"event_id":"drift-b","journey_id":"tj-dev-drift","tenant_id":"dev-smoke","environment":"paper","source":"smoke","stage":"signal_generation","stage_status":"succeeded","signal_id":"sig","occurred_at":"2026-07-13T00:00:30Z","recorded_at":"2026-07-13T00:00:02Z","sequence":2},
 {"event_id":"drift-a","journey_id":"tj-dev-drift","tenant_id":"dev-smoke","environment":"paper","source":"smoke","stage":"signal_generation","stage_status":"succeeded","signal_id":"sig","occurred_at":"2026-07-12T23:59:30Z","recorded_at":"2026-07-13T00:00:01Z","sequence":1},
]
m = JourneyMaterializer(); m.rebuild(events)
p = m.get("tj-dev-drift", tenant_id="dev-smoke", environment="paper")
assert [e["event_id"] for e in p.timeline] == ["drift-a", "drift-b"]
metrics = compute_data_quality_metrics([p], environment="paper", source_watermarks=m.source_watermarks, now=datetime(2026,7,13,0,1,tzinfo=timezone.utc))
incidents = evaluate_data_quality(metrics, load_slo_targets("paper"), [p], now=datetime(2026,7,13,0,1,tzinfo=timezone.utc))
assert metrics.clock_drift_event_count == 2
assert any(i.code == "clock_drift" and i.journey_id == "tj-dev-drift" for i in incidents)
print({"restart_replay": state, "clock_drift_event_count": metrics.clock_drift_event_count, "clock_drift_incident": True})
PY

echo "trade journey residual dev smoke: PASS commit=$(git rev-parse HEAD) key=$key"
