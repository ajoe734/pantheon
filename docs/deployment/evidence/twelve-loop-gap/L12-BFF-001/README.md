# L12-BFF-001 owner evidence

Status: BFF implementation ready; current IncidentCase authority dependency blocked.

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

On branch head `feeb580f81d14bd3527e5f1ed4c915c301af10be`, based on dev
`5a2fc69a3b432e0d1bc528981d66a2ee32defa71`:

```text
.venv-pantheon/bin/python -m pytest -q \
  services/control-plane/bff/test_bff_downstream_health_monitor.py \
  services/control-plane/bff/test_bff_v5_loop_sentinel_contract.py \
  services/telemetry/test_infrastructure_health_ingest.py

116 passed, 27 warnings
```

The tests include a monitor-built event admitted through the real strict
telemetry HTTP route and durable admission ledger, two monitor replicas sharing
one state store, restart readback, stable event ID dedupe, error-rate emission,
MFA/approval-bound DLQ replay, and a real local HTTP target stop/restart probe.

## Blocking composition boundary

Current `services/incidents/main.py` does not expose
`POST /api/incidents/consume-infrastructure-health`. Its generic create route
still requires RuntimeBinding evidence. `services/incidents` belongs to
`L12-EVO-001`, and its current PR #4267 does not add this route.

Consequently this task does not claim a current real IncidentCase creation. The
BFF keeps the non-trading incident intent durable and fail-closed; it will not
fall back to the rejected fake RuntimeBinding pattern. After the incidents owner
adds that authority, `L12-VERIFY-OBS-001` must run the hosted target
stop/recovery proof with the manifest-issued BFF service JWT and retained BFF
volume.

The machine-readable acceptance and independent-review placeholder are in
`evidence.json`.
