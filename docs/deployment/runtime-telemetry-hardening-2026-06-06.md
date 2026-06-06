# Runtime Telemetry Hardening - 2026-06-06

Task: OPS-RTEL-001

## Scope

This hardening pass makes telemetry durability part of deployment instead of a
manual recovery checklist:

- `scripts/db_migrate.sh` and `scripts/bootstrap.sh` create the canonical
  `telemetry_events` table plus operational indexes for event type, time,
  binding, runtime, deployment stage, and payload search.
- Telemetry `/readyz` and `/metrics` expose writer, buffer, DLQ, and startup
  replay counters so deployment health can show whether the writer is actually
  running.
- `scripts/bootstrap.sh` runs one safe DLQ replay pass after all services are
  healthy. The replay endpoint only replays write-failure entries by default.
- `TelemetryIngestService.start()` loads persisted DLQ spill entries so replay
  works after a process restart with the same telemetry storage volume.

## Deployment Flow

The default bootstrap sequence is now:

1. Start infra services.
2. Apply idempotent telemetry schema migrations.
3. Start application services and wait for `/readyz`.
4. POST `/api/telemetry/replay` inside the telemetry container.
5. Print final compose service status.

Use this command for the normal control-plane bring-up:

```bash
bash scripts/bootstrap.sh
```

If a deployment intentionally wants to defer telemetry DLQ replay to an
operator, use:

```bash
bash scripts/bootstrap.sh --skip-telemetry-replay
```

The service also supports optional replay on service startup:

```bash
TELEMETRY_REPLAY_DLQ_ON_START=true
```

Leave it unset for normal bootstrap-driven deployment. If
`TELEMETRY_REPLAY_DLQ_TAG` is set, startup replay uses that explicit tag;
otherwise it uses the safe write-failure default.

## Readiness Evidence

`GET /readyz` includes:

- `dependencies.telemetry_writer.status`
- `dependencies.telemetry_writer.running`
- `dependencies.dead_letter_queue.memory_entries`
- `metrics.writer_total_written`
- `metrics.writer_total_failed`
- `metrics.writer_total_retried`
- `metrics.writer_total_dlq`
- `metrics.dlq_memory_entries`
- `metrics.startup_dlq_loaded`
- `metrics.startup_dlq_replayed`

The telemetry writer dependency must be `ok` before deployment treats telemetry
as ready. DLQ counts are surfaced for operator action but do not by themselves
block readiness; bootstrap performs the replay pass explicitly.

## Replay Boundary

Default replay is intentionally narrow:

- replayed by default: `writer_error`, `retry_exhausted`
- not replayed by default: `schema_violation`, `binding_mismatch`,
  `temporal_violation`

All replayed events re-enter the full `ingest()` path, so schema and evidence
validation still run before the event is re-buffered for the writer. The
Postgres writer remains idempotent through `ON CONFLICT (event_id) DO NOTHING`.

## Validation

Focused local validation for this task:

```bash
python3 -m unittest \
  services.telemetry.test_ingest_shock_absorption \
  services.telemetry.test_main_routes
```

Deployment script syntax check:

```bash
bash -n scripts/bootstrap.sh
bash -n scripts/db_migrate.sh
```
