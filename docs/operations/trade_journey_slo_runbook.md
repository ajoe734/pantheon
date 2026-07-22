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
and emits `DataQualityIncident` records.

Reviewer follow-up on PR #3460 found that this pure evaluator had no runtime
wiring: no exporter, no dashboard/alert-rule artifact, and no alert
transport that consumed an emitted incident. That gap is closed by four
additions, all covered by `test_alert_rules.py` / `test_dashboard.py` /
`test_alert_transport.py` / `services/control-plane/bff/test_tj_e2e_011_slo_alert_endpoint.py`:

- **Runtime exporter** — `GET /bff/management/trade-journeys/slo`
  (`services/control-plane/bff/trade_journeys.py`) computes metrics/incidents
  from the *live* materializer (not a synthetic drill), scoped by
  `tenant_id`/`environment` and RBAC like every other route in this router
  family.
- **Alert-rule artifact** — `trade_journey_alert_rules.json` +
  `alert_rules.py` drive the six aggregate SLO-breach checks
  (materializer lag, correlation/reconciliation completeness, SSE
  disconnects, detail/resolve API p95) from data, not hardcoded Python
  thresholds. Per-journey diagnostics (stalled/orphan/identifier-conflict/
  conflicting-terminal/reconciliation-mismatch/broker-reject) stay in
  `evaluate_data_quality` because they require per-projection state.
- **Dashboard artifact** — `trade_journey_slo_dashboard.json` +
  `dashboard.py` name every `DataQualityMetrics` field as a panel;
  `validate_dashboard()` fails if a panel drifts from a real field, and
  `render_dashboard_snapshot()` populates live values/targets on every
  `/slo` call.
- **Alert transport** — `alert_transport.py`'s `DataQualityAlertTransport`
  publishes every emitted incident onto the same shared outbox primitive
  other Pantheon services use (`services/foundation/outbox.py`,
  `JsonlOutboxStore`), so an incident has a durable, replayable record
  instead of vanishing when the request returns.

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
| Stage latency | `stage_latency_ms` | per-stage p50/p95/sample-count of `recorded_at - occurred_at`, same shape as the TJ-E2E-005 `/metrics` endpoint's `stage_latency_ms` |
| Journey detail API p95 | `detail_api_p95_ms` | p95 of real `detail`/`{journey_id}` handler latency, recorded by `ApiLatencyRecorder` and passed in via `detail_api_latencies_ms` |
| Identifier resolve API p95 | `resolve_api_p95_ms` | p95 of real `resolve` handler latency, recorded the same way via `resolve_api_latencies_ms` |

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
`identifier_conflict`, `conflicting_terminal_states`,
`reconciliation_mismatch`, `broker_reject`) carries `journey_id`,
`tenant_id`, `environment`, and `evidence_ref` pointing at
`/bff/management/trade-journeys/{journey_id}/evidence` (the TJ-E2E-005
evidence route) — the operator can jump straight from the alert to the
journey's evidence bundle. Aggregate SLO-breach incidents
(`materializer_lag_breach`, `correlation_completeness_breach`,
`reconciliation_completeness_breach`, `sse_disconnect`,
`detail_api_p95_breach`, `resolve_api_p95_breach`) are environment-scoped
and route to `/bff/management/trade-journeys/attention`.

Each `DataQualityIncident.alert_path` carries `event_type`,
`severity_channel` (default `telemetry.alerts`), `operator_surface`,
`escalation_target`, and this runbook's path.

`GET /bff/management/trade-journeys/slo?tenant_id=...&environment=...`
(same RBAC/tenant-scope contract as every other route in
`trade_journeys.py`) is the runtime integration point: it computes metrics
and incidents from the live materializer, publishes every incident through
`DataQualityAlertTransport.publish_incidents()`
(`services/trade_journey/alert_transport.py`), and returns the metrics,
incidents, `alerts_published` count, and a rendered dashboard snapshot in
one response. The alert transport writes each incident as an
`EventEnvelope`/`OutboxRecord` to a `JsonlOutboxStore` at
`$PANTHEON_TRADE_JOURNEY_SLO_DATA_DIR/slo_alerts_outbox.jsonl` (defaults to
`/tmp/pantheon/trade_journey_slo/slo_alerts_outbox.jsonl`) — an on-call
consumer tails or replays that file the same way other Pantheon services
consume their own outbox.

## Failure Injection

`services/trade_journey/failure_injection.py` runs synthetic drills that
must trigger the matching alert, satisfying the TJ-E2E-011 acceptance
criterion ("故障注入可觸發 stalled/orphan/conflict/lag 告警"):

| Scenario | Expected alert |
|---|---|
| `materializer_lag` | `materializer_lag_breach` |
| `orphan_identifier` | `orphan_identifier` |
| `identifier_conflict` | `identifier_conflict` |
| `conflicting_terminal` | `conflicting_terminal_states` |
| `stalled` | `journey_stalled` |
| `reconciliation_mismatch` | `reconciliation_mismatch` |
| `broker_reject` | `broker_reject` |
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
  revert or edit `trade_journey_slo_targets.json`/
  `trade_journey_alert_rules.json`, or stop calling
  `DataQualityAlertTransport.publish_incidents()` from the BFF `/slo` route.
  No producer, materializer, or other BFF route change is required to roll
  back alerting; the `/slo` route itself can be disabled independently since
  it only reads the materializer and writes to its own outbox file.

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
- `detail_api_p95_ms`/`resolve_api_p95_ms` reflect whatever samples
  `ApiLatencyRecorder` has captured in the current BFF process since its
  last restart (bounded ring buffer, default 500 samples per endpoint) —
  they are not a durable, cross-restart time series.

## Verification

Focused tests:

```bash
python3 -m pytest -q services/trade_journey/test_slo_data_quality.py
python3 -m pytest -q services/trade_journey/test_alert_rules.py
python3 -m pytest -q services/trade_journey/test_dashboard.py
python3 -m pytest -q services/trade_journey/test_alert_transport.py
python3 -m pytest -q services/control-plane/bff/test_tj_e2e_011_slo_alert_endpoint.py
python3 -m pytest -q services/trade_journey/
python3 -m services.trade_journey.failure_injection
```
