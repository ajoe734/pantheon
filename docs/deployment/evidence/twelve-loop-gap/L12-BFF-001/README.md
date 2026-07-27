# L12-BFF-001 owner evidence

Status: BFF implementation ready; IncidentCase authority composed locally.

Delivery: [PR #4274](https://github.com/ajoe734/pantheon/pull/4274)
targets `dev`; the prior AC5 blocker has been resolved in the PR branch and
now awaits independent review plus normal PR gates.

The BFF health controller now emits the strict non-trading
`pantheon.infrastructure-health/1` contract, enumerates the complete configured
downstream registry, and persists probe windows, target state, error-rate
windows, delivery intent, incident mappings, claims, retries, dead letters and
replay audit in a shared SQLite WAL store under `BFF_DATA_DIR`.

Telemetry delivery uses a service JWT plus explicit tenant and producer
authority. Stable event IDs are derived from tenant, producer, component, probe
kind and probe window. Telemetry, incident-open and incident-resolve deliveries
reuse that source event ID and are dependency ordered. A recovery never clears
the incident mapping before the incidents authority confirms the transition.

`POST /bff/v5/downstream-health/dlq/replay` is operator-only, MFA-bound, and
requires `approval_ref` plus `reason`; the actor and replay result are persisted.

## Verified

On implementation head `f2be990d53f041cb52d6eadf4832b8873baab6b0`, based on dev
`b81edf76dfc14087dd7d5e3a6599448cb9d0bb09`:

```text
/home/lupin/pantheon/.venv/bin/python -m pytest -q \
  services/control-plane/bff/test_bff_downstream_health_monitor.py \
  services/control-plane/bff/test_bff_v5_loop_sentinel_contract.py \
  services/telemetry/test_infrastructure_health_ingest.py \
  services/incidents/test_main_routes.py

167 passed, 29 warnings
```

The tests include a monitor-built event admitted through the real strict
telemetry HTTP route and durable admission ledger, two monitor replicas sharing
one state store, restart readback, stable event ID dedupe, error-rate emission,
MFA/approval-bound DLQ replay, a real local HTTP target stop/restart probe, and
the incidents-owned non-trading `POST /api/incidents/consume-infrastructure-health`
route. The post-review repair also proves incident authority HTTP 409 is not
treated as an idempotent success, leaves conflicting delivery intent visible in
DLQ, and bounds retained durable history with trigger-maintained delivery
status counters so health reads do not scale with outbox history.

## Incident authority composition

`services/incidents/main.py` now exposes
`POST /api/incidents/consume-infrastructure-health`. The route is separate from
generic `POST /api/incidents`: the BFF payload does not include
`binding_id`/`deployment_stage`, and the incident authority maps
`tenant_id + producer + component + source_event_id` into a stable non-trading
infrastructure incident namespace.

The route creates the real `IncidentCase`, treats exact replay as idempotent,
rejects conflicting replay, ignores caller-supplied fake RuntimeBinding fields,
and resolves through the canonical status route. `L12-VERIFY-OBS-001` still
must run the hosted target stop/recovery proof with the manifest-issued BFF
service JWT and retained BFF volume before hosted deployment closeout.

The machine-readable acceptance and independent-review placeholder are in
`evidence.json`.
