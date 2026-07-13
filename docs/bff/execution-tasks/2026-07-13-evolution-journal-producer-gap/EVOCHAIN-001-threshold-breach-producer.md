# EVOCHAIN-001 — Threshold-breach producer (telemetry -> incidents)

Status: implemented, pending Codex review

Owner: Claude
Reviewer: Codex
Wave: 0
Depends on: none

Source gap spec: `docs/04/pantheon_evolution_journal_producer_gap_2026-07-13/EVOLUTION_JOURNAL_PRODUCER_GAP.md`
Execution packet: `docs/bff/execution-tasks/2026-07-13-evolution-journal-producer-gap/INDEX.md`

## Problem

`services/incidents/consumer.py` (`ThresholdTelemetryIncidentConsumer`) is a
complete adapter with zero callers. Nothing evaluates live paper telemetry
against governance thresholds and posts breach payloads, so the incident ->
postmortem -> evolution -> journal chain never fires from real data.

## What shipped

- `services/evolution/threshold_sweep_worker.py` — the producer. Reads
  per-binding/per-persona paper performance summaries from the telemetry
  read path (`GET {telemetry}/api/telemetry/runtime-summaries`, the same
  summaries the performance console reads — see
  `services/control-plane/bff/read_store.py` `_HTTP_DATASETS["telemetry_summaries"]`),
  evaluates them against live-config thresholds shaped like the governance
  `ThresholdSnapshot` schema (`services/control-plane/governance/evolution_decision.py`),
  and POSTs any breach to `POST {incidents}/api/incidents/consume-threshold`
  (`services/incidents/consumer.py::ThresholdTelemetryIncidentConsumer`).
  Talks to both services over HTTP only — no cross-service Python imports,
  per the Incident service's own write-authority rule.
- `services/evolution/config/threshold_sweep_thresholds.json` — live config:
  the threshold list (`metric_name`, `signal_type`, `policy_source`,
  `summary_field`, `comparator`, `threshold_value`, `window`). Ships with two
  entries derived from `EVOLUTION_REVIEW_AND_THRESHOLDS.md` section 7.1:
  `rolling_drawdown_multiple` (`drawdown` > 1.25) and `rolling_pnl_floor`
  (`pnl` < -500.0, an operator-tunable placeholder — no absolute PnL floor is
  documented in the v1 threshold spec, only the drawdown multiplier).
- `docker-compose.yml` — new `evolution-threshold-sweep-producer` service.
  Not gated behind a profile (default-on, like `reconciliation-drift-svc`).
  Bind-mounts `services/evolution/config` read-only so operators can retune
  threshold values by editing the host file and restarting the one service —
  no image rebuild. Own interval env
  (`EVOCHAIN_THRESHOLD_SWEEP_INTERVAL_SECONDS`, default `86400`); does not
  touch `EVOLUTION_SCHEDULER_INTERVAL_SECONDS` or any other existing cadence.
- `services/evolution/test_threshold_sweep_worker.py` — 17 tests.

## Idempotency

Dedupe key: `(binding_id, metric_name, threshold window, UTC day bucket)`.
The worker hashes this key into a deterministic telemetry `event_id`
(`tel-threshold-sweep-<uuid5>`). The incidents consumer already derives
`incident_id` deterministically from `event_id` + `metric_name`
(`services/incidents/consumer.py::_incident_id`), so a rerun within the same
day for the same binding/metric resolves to the same `incident_id` and the
consumer's existing-incident check returns `created=False` instead of
duplicating. The dedupe key is also written into the incident's
`threshold_snapshot.note` (`dedupe_key=...`) for operator traceability.
Verified in `test_payload_accepted_by_real_consumer_and_idempotent_on_rerun`
and `test_run_tick_creates_then_dedupes_on_rerun_via_real_consumer`.

## Fail-closed behavior

Nothing is ever fabricated as a breach:

- live config missing/unreadable/malformed -> `load_thresholds` returns `[]`,
  `run_tick` logs a diagnostic and skips the tick.
- telemetry unreachable -> `run_tick` logs a diagnostic and skips the tick
  (never calls `post_incident`).
- a runtime summary missing any required identity field (`binding_id`,
  `runtime_id`, `deployment_stage`, `deployment_plan_id`, `capital_pool_id`,
  `persona_capital_binding_id`, `artifact_id`, `artifact_version`) is skipped
  with a diagnostic.
- a threshold's `summary_field` missing or non-numeric on a summary is
  skipped with a diagnostic.

Verified in `test_load_thresholds_missing_file_fails_closed`,
`test_load_thresholds_malformed_json_fails_closed`,
`test_evaluate_breaches_missing_identity_field_is_diagnostic_only`,
`test_evaluate_breaches_missing_metric_field_is_diagnostic_only`,
`test_evaluate_breaches_non_numeric_metric_is_diagnostic_only`,
`test_run_tick_fails_closed_when_no_thresholds_configured`,
`test_run_tick_fails_closed_when_telemetry_fetch_errors`.

## Local validation

```sh
python3 -m pytest services/evolution/test_threshold_sweep_worker.py -q
# 17 passed

python3 -m pytest services/incidents -q
# 46 passed (no regression in the consumer this producer drives)

docker compose config --services | grep evolution-threshold-sweep-producer
# evolution-threshold-sweep-producer
```

## Acceptance mapping

| Acceptance criterion | Where |
|---|---|
| producer evaluates live paper telemetry aggregates against governance-schema thresholds from live config | `threshold_sweep_worker.load_thresholds` + `evaluate_breaches`, config in `services/evolution/config/threshold_sweep_thresholds.json` |
| breach POSTs canonical payload accepted by `ThresholdTelemetryIncidentConsumer` and creates an `IncidentCase` | `default_post_incident` -> `POST /api/incidents/consume-threshold`; proven end-to-end against the real consumer in tests |
| re-runs do not duplicate open incidents for the same binding/metric/window (dedupe key recorded) | deterministic `event_id`/`incident_id`; `dedupe_key` in `threshold_snapshot.note` |
| missing or ambiguous telemetry emits diagnostics and produces no incident | fail-closed paths above |
| compose service ships with `EVOCHAIN_THRESHOLD_SWEEP_INTERVAL_SECONDS` default 86400 and its own logs | `docker-compose.yml` `evolution-threshold-sweep-producer`; `main()` prints one JSON line per tick to stdout |

## Residual risk

- The `rolling_pnl_floor` default (`-500.0`) is a placeholder, not a
  governance-approved canonical number (the v1 threshold spec only documents
  the drawdown multiplier). Owner: Human/Ops. Expiry: before EVOCHAIN-011
  (dev deploy) enables this service against real capital-scale paper
  runtimes — retune via the bind-mounted config, no code change needed.
- This task does not enable the daily sweep scheduler
  (`evolution-daily-sweep-scheduler` is still profile-gated) or deploy to
  dev; that is EVOCHAIN-002 and EVOCHAIN-011 respectively.
