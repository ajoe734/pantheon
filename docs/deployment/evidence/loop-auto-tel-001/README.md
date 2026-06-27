# LOOP-AUTO-TEL-001 Evidence

Date: 2026-06-27
Owner: Codex
Reviewer: Claude

## Scope

Task: Audit telemetry readiness and writer durability.

Changed runtime scope:

- `services/telemetry/main.py`: `/readyz` now includes a
  `canonical_telemetry_table` dependency that probes `telemetry_events` when
  `TELEMETRY_DB_DSN` is configured. Missing table or missing required columns
  reports dependency `status=error`, so `/readyz` returns 503.
- `services/telemetry/batch_writer.py`: writer stats now expose write freshness
  and failure/DLQ timestamps.
- `services/telemetry/ingest_svc.py`: DLQ replay candidates are deduplicated by
  `event_id` even when an explicit tag filter is supplied.

Not changed:

- No live-capital execution.
- No approval gate bypass.
- No schema migration broadening beyond the existing `scripts/db_migrate.sh`
  bootstrap contract.
- No change to telemetry event schema semantics.

## Acceptance Mapping

1. Telemetry ready fails when canonical table is missing.
   - Covered by `services.telemetry.test_main_routes.TestMainRoutes.test_readyz_fails_when_canonical_table_missing`.
   - Expected result: `/readyz` returns 503 with
     `dependencies.canonical_telemetry_table.status=error`.

2. Writer metrics expose failure DLQ and freshness.
   - Covered by `test_readyz_exposes_writer_and_dlq_metrics`.
   - Covered by `TestAsyncBatchWriter.test_retry_and_dlq_on_permanent_failure`
     and `test_partition_routing`.
   - Exposed fields include `failure_dlq_entries`,
     `last_successful_write_at`, `last_failed_write_at`, `last_dlq_at`, and
     `seconds_since_last_*` gauges.

3. DLQ replay is idempotent by event id.
   - Covered by `TestReplayPolicy.test_replay_deduplicates_across_write_failure_tags`.
   - Covered by `TestReplayPolicy.test_replay_with_explicit_tag_filter_deduplicates_event_id`.

## Verification

Commands run from repo root:

```bash
python3 -m unittest services.telemetry.test_main_routes services.telemetry.test_ingest_shock_absorption
python3 services/telemetry/smoke_test_ingest.py
```

Result:

- `services.telemetry.test_main_routes` plus
  `services.telemetry.test_ingest_shock_absorption`: 75 tests, OK.
- `services/telemetry/smoke_test_ingest.py`: all 4 smoke checks passed.

## Operational Note

`scripts/db_migrate.sh` remains the canonical bootstrap path for
`telemetry_events`. With `TELEMETRY_DB_DSN` configured, readiness now fails
closed until that bootstrap has created the table with the required columns.
