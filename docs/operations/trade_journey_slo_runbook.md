# Trade Journey SLO And Data-Quality Incident Runbook

Task: `TJ-E2E-011`

## Scope

This runbook covers materializer lag, correlation completeness, stalled
stages, orphan events, conflicting terminal states, broker rejects, and
reconciliation mismatch aging for the Trade Journey read model
(`services/trade_journey/materializer.py`, TJ-E2E-004) and its BFF surface
(`services/control-plane/bff/trade_journeys.py`, TJ-E2E-005/007).

The evaluation logic is a pure, side-effect-free module,
`services/trade_journey/slo_data_quality.py`. It reads a
`JourneyMaterializer` snapshot plus `source_watermarks`, computes the
metrics required by the Trade Journey E2E observability gap spec section 13
(`docs/04/pantheon_trade_journey_e2e_observability_gap_2026-07-11/TRADE_JOURNEY_E2E_OBSERVABILITY_GAP.md`),
and emits `DataQualityIncident` records. It does not write to a store, call
a broker, or push to an external alert transport — the operator/on-call
pipeline is responsible for taking `DataQualityIncident.alert_path` and
routing it to `severity_channel`.

## Metrics Measured

Computed by `compute_data_quality_metrics()`:

| Metric | Field | Source |
|---|---|---|
| Materializer lag | `materializer_lag_seconds` | `now - min(source_watermarks.values())` |
| Stalled count | `stalled_count` | journeys past `stalled_after_seconds` and not terminal |
| Orphan event rate | `orphan_event_rate` | share of journeys with the `orphan_identifier` diagnostic |
| Missing identifier rate | `missing_identifier_rate` | share of journeys missing the canonical identifier for a stage they have reached (`STAGE_IDENTIFIER_EXPECTATIONS`) |
| Identifier conflict rate | `identifier_conflict_rate` | share of journeys with the `identifier_conflict` diagnostic |
| Conflicting terminal rate | `conflicting_terminal_rate` | share of journeys with the `conflicting_terminal_states` diagnostic |
| Correlation completeness | `correlation_completeness_rate` | `1 - missing_identifier_rate` |
| Reconciliation completeness | `reconciliation_completeness_rate` | share of executed journeys (reached `order_submission`) that reached `reconciliation` |
| Broker reject rate | `broker_reject_rate` | share of journeys that reached `broker_acknowledgement` and were rejected there |
| Partial fill aging | `partial_fill_max_age_seconds` | oldest `partially_filled` journey age |
| Reconciliation mismatch aging | `reconciliation_mismatch_max_age_seconds` | oldest `completed_with_variance` journey age |
| Late-event lag | `late_event_lag_p95_ms` | p95 of `recorded_at - occurred_at` across the timeline |
| SSE disconnects | `sse_disconnect_count` | supplied by the BFF SSE connection registry (not derived from the materializer) |

## Default SLO Targets

Source: `services/trade_journey/trade_journey_slo_targets.json`, values from
the gap spec section 13 table. `live` shares the `canary` row.

| Metric | Paper | Canary/Live |
|---|---:|---:|
| Producer event → read model p95 | ≤ 10s | ≤ 3s |
| Journey detail API p95 | ≤ 1.5s | ≤ 1.0s |
| Any-ID resolve p95 | ≤ 1.0s | ≤ 1.0s |
| Correlation completeness | ≥ 99% | ≥ 99.9% |
| Reconciliation completeness | ≥ 99% | 100% or an explicit incident |
| Stalled threshold | 900s | 300s |

Operations/risk own the authoritative values; edit
`trade_journey_slo_targets.json` and re-run the verification command below
to confirm the config still loads and passes the test suite.

## Alert Routing

Every journey-scoped incident (`journey_stalled`, `orphan_identifier`,
`conflicting_terminal_states`, `reconciliation_mismatch`, `broker_reject`)
carries `journey_id`, `tenant_id`, `environment`, and `evidence_ref` pointing
at `/bff/management/trade-journeys/{journey_id}/evidence` (the TJ-E2E-005
evidence route) — the operator can jump straight from the alert to the
journey's evidence bundle. Aggregate SLO-breach incidents
(`materializer_lag_breach`, `correlation_completeness_breach`,
`reconciliation_completeness_breach`, `sse_disconnect`) are
environment-scoped and route to `/bff/management/trade-journeys/attention`.

Each `DataQualityIncident.alert_path` carries `event_type`,
`severity_channel` (default `telemetry.alerts`), `operator_surface`,
`escalation_target`, and this runbook's path. Wire `alert_path.event_type`
to the operator's existing alert transport; this module does not publish
alerts itself.

## Failure Injection

`services/trade_journey/failure_injection.py` runs synthetic drills that
must trigger the matching alert, satisfying the TJ-E2E-011 acceptance
criterion ("故障注入可觸發 stalled/orphan/conflict/lag 告警"):

| Scenario | Expected alert |
|---|---|
| `materializer_lag` | `materializer_lag_breach` |
| `orphan_identifier` | `orphan_identifier` |
| `conflicting_terminal` | `conflicting_terminal_states` |
| `stalled` | `journey_stalled` |
| `reconciliation_mismatch` | `reconciliation_mismatch` |
| `stale_sse` | `sse_disconnect` |

Run all drills:

```bash
python3 -m services.trade_journey.failure_injection
```

Run one scenario against the paper targets:

```bash
python3 -m services.trade_journey.failure_injection --scenario stalled --environment paper
```

Exit code is non-zero if any scenario fails to trigger its expected alert.
The harness only builds in-memory synthetic events through a fresh
`JourneyMaterializer`; it never calls a broker or writes to a shared store.

## Capacity / Rebuild Baseline

`services/trade_journey/capacity_baseline.py` measures
`JourneyMaterializer.rebuild()` throughput so a disaster-rebuild drill has a
number to compare against. Recorded baseline (single-process, dev worktree,
2026-07-12):

| Journeys | Stages/journey | Events | Elapsed | Events/sec |
|---:|---:|---:|---:|---:|
| 500 | 10 | 5,000 | 0.114s | ~43,800 |
| 2,000 | 14 (full journey) | 28,000 | 0.590s | ~47,400 |

Re-run before a capacity review or after a materializer schema change:

```bash
python3 -m services.trade_journey.capacity_baseline --journeys 2000 --stages-per-journey 14
```

A large regression in `events_per_second` against this baseline is itself a
capacity incident — investigate before the next scheduled rebuild.

## Rollout And Rollback

Per gap spec section 19:

- The materializer is rebuildable from source events at any time and must
  never become a synchronous dependency of the execution plane — a stalled
  or failed rebuild degrades the read model, not order flow.
- New Trade Journey UI, SSE, or governed actions must be disableable via
  feature flag without stopping execution producers. Disabling the SSE feed
  or the frontend routes does not affect `JourneyMaterializer.ingest()`,
  which keeps consuming producer events regardless of read-side traffic.
  That feature flag is owned by the SSE/BFF operator (TJ-E2E-007), not this
  module, and toggling it never requires restarting the materializer or the
  ingest path.
- If `rebuild_status` reports `failed`, the previous in-memory projections
  remain until a corrected rebuild succeeds — `rebuild()` clears
  `_events`/`_event_fingerprints` before repopulating, so a failed rebuild
  should be retried with corrected input rather than partially patched.
- Rollback of this module (the SLO/alert layer itself) is config-only:
  revert or edit `trade_journey_slo_targets.json` and stop routing
  `alert_path.event_type` values to the alert transport. No producer,
  materializer, or BFF route change is required to roll back alerting.

## Operational Notes

- `compute_data_quality_metrics()` and `evaluate_data_quality()` take `now`
  and `source_watermarks` as explicit parameters rather than reading the
  wall clock, so evaluation is deterministic in tests, drills, and replay.
- `missing_identifier_rate` (and therefore `correlation_completeness_rate`)
  is derived from `STAGE_IDENTIFIER_EXPECTATIONS`, which only covers stages
  with a dedicated canonical identifier field in
  `services/trade_journey/materializer.py::IDENTIFIER_FIELDS`. Extending
  correlation coverage to a new stage requires adding both the identifier
  field to the materializer and the stage mapping here.
- `sse_disconnect_count` is not derived from the materializer; the BFF SSE
  connection registry (TJ-E2E-007) must supply it when calling
  `compute_data_quality_metrics()` in production wiring.
- Incidents are pure data — this module does not decide retry, throttling,
  or auto-remediation. An operator or the alert transport's own policy
  decides response actions.

## Verification

Focused tests:

```bash
python3 -m pytest -q services/trade_journey/test_slo_data_quality.py
python3 -m pytest -q services/trade_journey/
python3 -m services.trade_journey.failure_injection
```
