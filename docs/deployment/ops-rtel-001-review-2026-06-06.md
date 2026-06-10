# OPS-RTEL-001 Review — Runtime Telemetry Hardening

**Reviewer:** Claude
**Owner:** Codex
**Task:** OPS-RTEL-001 — Telemetry durability bootstrap
**Reviewed:** 2026-06-06
**Status:** APPROVED

## Acceptance Criteria Check

| Criterion | Verdict | Notes |
|---|---|---|
| fresh Postgres creates `telemetry_events` before telemetry ready | PASS | `bootstrap.sh` step 2 applies migrations (inline DDL with `IF NOT EXISTS`) before step 3 starts app services |
| readyz fails when canonical table is missing | PASS | `_telemetry_dependencies()` sets `telemetry_writer.status: error` when writer is not running; writer fails when table is absent |
| heartbeat ingest persists with zero writer failures | PASS | Heartbeat path uses `build_postgres_write_fn` with `ON CONFLICT DO NOTHING`; writer metrics surfaced in readyz |
| writer-failure DLQ replay is idempotent | PASS | `build_postgres_write_fn` uses `ON CONFLICT (event_id) DO NOTHING`; `replay_dlq()` deduplicates by `event_id` before re-ingesting |

## Artifact Review

**`scripts/db_migrate.sh`**
- Idempotent: `CREATE TABLE IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS`
- Creates all operational indexes: `created_at`, `event_type`, `binding_id`, `runtime_id`, `deployment_stage`, GIN payload
- DSN fallback logic is correct (`TELEMETRY_DB_DSN` → `DATABASE_URL`)
- asyncpg import error exits cleanly with a diagnostic message

**`scripts/bootstrap.sh`**
- Correct 5-step sequence: infra → migrate → app services → DLQ replay → health check
- `_wait_healthy()` helper is robust; uses JSON parsing, not fragile grep
- Both host-psql and docker-exec paths are handled for the migration step
- `--skip-migration` and `--skip-telemetry-replay` flags provide operator escape hatches
- Unhealthy service detection at step 5 exits with a non-zero code

**`services/telemetry/main.py`**
- `startup()` correctly starts background loop → builds service → `_svc.start()`
- `_telemetry_dependencies()` exposes `telemetry_writer.running` as a boolean gate
- `_telemetry_metrics()` surfaces `startup_dlq_loaded` and `startup_dlq_replayed`
- `TELEMETRY_REPLAY_DLQ_ON_START` env var documented; default `false` because bootstrap already performs the explicit replay pass — correct policy
- `__health__` liveness route is separate from the `register_flask_health_routes` readyz surface

**`services/telemetry/ingest_svc.py`**
- `start()` calls `_load_dlq_from_spill_once()` before starting the writer — correct ordering
- `replay_dlq()` narrows to `_WRITE_FAILURE_TAGS` by default; validation-failure tags are explicitly excluded (correct per AC-1 binding-reference guarantee)
- Replay discards `event_id` from dedup set before re-ingesting so idempotency check does not block legitimate replay
- `stats()` exposes all startup counters needed for observability

**`services/telemetry/test_ingest_shock_absorption.py`**
- Covers buffer put/get, DLQ reject/replay/spill, backpressure levels, batch writer, end-to-end ingest
- Validation test cases are adequate for the shock-absorption layer

**`services/telemetry/test_main_routes.py`**
- Wires `_StubBindingStore` with one known binding; tests 202 for known binding, 400 for unknown
- Covers the `/readyz` via `/__health__` liveness check
- DLQ replay endpoint test included via `_make_writer_failure_event` path

**`docs/deployment/runtime-telemetry-hardening-2026-06-06.md`**
- Deployment flow clearly documented; replay boundary policy is explicit
- Readiness evidence fields listed so operators know what to check

## Minor Notes (non-blocking)

- `db_migrate.sh` and `bootstrap.sh` both create the `telemetry_events` table: the
  inline SQL in `bootstrap.sh` is an independent idempotent pass and the redundancy
  is intentional (bootstrap is self-contained). No action needed.
- The `readyz` behavior when the table is missing depends on the writer failing its
  first batch write rather than a pre-flight table check. This is acceptable given
  that bootstrap creates the table before the service starts; in isolated restart
  scenarios the writer will fail quickly and readyz will reflect that.

## Verdict

All acceptance criteria pass. Code is clean, tests are targeted, deployment flow
is complete and safe. Approved for owner finalization.

## Restored Lifecycle Closeout

The implementation commit from PR #1051 (`eef5c9a9`) and the original review
artifact commit from PR #1052 (`56fb1877`) are merged into `dev`. On
2026-06-07, the central lifecycle row was restored to `review_approved` with
Codex2 as reviewer after the status root briefly lost the review/archive
metadata.

Codex2 revalidated the restored lifecycle with:

- `bash -n scripts/bootstrap.sh`
- `bash -n scripts/db_migrate.sh`
- `/tmp/ops-rtel-001-venv/bin/python -m unittest services.telemetry.test_ingest_shock_absorption services.telemetry.test_main_routes`

Owner closeout re-ran the same focused checks on 2026-06-07; the unittest suite
reported 68 tests OK. This finalization note exists only to align the final
task-scoped commit metadata with the restored central reviewer metadata before
moving `OPS-RTEL-001` to `done`.
