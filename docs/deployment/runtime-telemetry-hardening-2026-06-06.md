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

---

# Paper Runtime Fleet Reconciler — OPS-RTEL-002

Task: OPS-RTEL-002

## Scope

Adds `PaperFleetReconciler` which automatically maintains one worker subprocess
per active paper `RuntimeBinding`, replacing manual `docker run` for paper workers.

- `services/execution/runtime-manager/paper_fleet_reconciler.py` — reconciler
  implementation. Polls runtime-manager every `RECONCILER_POLL_INTERVAL_SECONDS`
  (default 15 s) for active paper bindings, starts/stops worker subprocesses,
  and restarts dead workers up to `RECONCILER_MAX_RESTARTS` (default 5) with
  linear backoff of `restart_count * RECONCILER_RESTART_BACKOFF_SECONDS` (default 5 s).
- `services/execution/runtime-manager/Dockerfile` — Python 3.11-slim image for
  the reconciler container; installs `redis` and `jsonschema` for spawned workers.
- `services/execution/runtime-manager/requirements.txt` — container dependencies.
- `services/execution/runtime-manager/test_paper_fleet_reconciler.py` — 25 unit
  tests covering start, stop, restart, port allocation, env builder, snapshot,
  degraded-fetch safety, restart backoff, binding-scoped signal queue
  isolation, and monitoring session lifecycle (open/stale-reap/restart).
- `docker-compose.yml` — adds `paper-fleet-reconciler` service under the
  `paper-fleet` profile. Activate with:

```bash
docker compose --profile paper-fleet up paper-fleet-reconciler
```

## Reconciler Behaviour

Each reconcile cycle:

1. `GET /api/runtime-bindings` on the runtime-manager (bearer-auth).
2. If the fetch fails (network error, timeout, non-200), the cycle preserves all
   currently running workers unchanged and retains `last_error` in the snapshot.
   No workers are started or stopped when desired state is unknown.
3. Filter for `deployment_mode == "paper"` and `status == "active"`.
4. For each desired binding with no live worker: spawn a subprocess running
   `paper_runtime.py` with binding context in env vars.
5. For each running worker whose binding is no longer active: send SIGTERM,
   wait `RECONCILER_DRAIN_TIMEOUT_SECONDS`, then SIGKILL if still running.
6. Detect and log process exits; auto-restart up to the configured cap with
   linear backoff: a worker that has restarted `N` times waits at least
   `N × RECONCILER_RESTART_BACKOFF_SECONDS` (default 5 s) before the next
   restart attempt.  The first restart (`N=0`) is always immediate.

## Health Surface

- `GET /healthz` — `200` once the first reconcile cycle completes without error.
- `GET /readyz` — same semantics as `/healthz`.
- `GET /livez` — always `200`.
- `GET /api/fleet/state` — full reconciler snapshot including per-worker pid,
  port, restart count, and status.

## Signal Queue Isolation

Each spawned worker receives a binding-scoped Redis signal queue key via
`PANTHEON_SIGNAL_QUEUE_KEY=pantheon:signals:pending:<binding_id>`. The
`RedisPendingSignalStore` inside `paper_runtime.py` reads this env var so
it only consumes signals for its own binding. Without this isolation, all
workers sharing the same Redis host would race to consume from the shared
default queue.

## Validation

```bash
python3 -m pytest services/execution/runtime-manager/test_paper_fleet_reconciler.py -v
# Expected: 25 passed
```

---

# Runtime-Aware Signal Isolation — OPS-RTEL-004

Task: OPS-RTEL-004

## Problem

With a 15-runtime paper fleet, all workers sharing the bare Redis key
`pantheon:signals:pending` race to consume signals. A signal published for
binding `b-001` can be consumed by any other runtime before the intended
worker drains it.

## Solution: Two-Layer Isolation

### Layer 1 — Binding-scoped queue key (queue-level isolation)

Each runtime must use a per-binding Redis key:
`pantheon:signals:pending:<binding_id>`.

**Reconciler (OPS-RTEL-002, already done):** `PaperFleetReconciler` sets
`PANTHEON_SIGNAL_QUEUE_KEY=pantheon:signals:pending:<binding_id>` in each
spawned worker's environment.

**Auto-derive (this task):** `build_pending_signal_store()` now resolves the
queue key in priority order when the default bare key is used:
1. `PANTHEON_SIGNAL_QUEUE_KEY` env var (set by reconciler)
2. Binding-scoped key derived from `PANTHEON_RUNTIME_BINDING_ID` env var
3. Bare default `pantheon:signals:pending` (standalone / test runs)

`PaperRuntimeService` also performs explicit derivation for clarity:

```python
# Resolve queue key: explicit env > binding-scoped > default
_explicit_key = os.getenv("PANTHEON_SIGNAL_QUEUE_KEY", "").strip()
_binding_for_key = (self._identity.binding_id or "").strip()
if _explicit_key:
    _resolved_queue_key = _explicit_key
elif _binding_for_key:
    _resolved_queue_key = binding_queue_key(_binding_for_key)
else:
    _resolved_queue_key = BINDING_QUEUE_KEY_PREFIX
```

### Layer 2 — Signal-level binding filter (defense-in-depth)

`SignalConsumer` now accepts a `binding_id` parameter. When set, signals
whose `binding_id` field does not match are discarded with a warning before
execution. Signals without a `binding_id` field are legacy/unrouted and
always pass through.

`PaperRuntimeService` passes `binding_id=self._identity.binding_id` to the
consumer automatically.

## Schema Change

`services/research/schema.json` gains two optional routing fields:
- `binding_id` — the RuntimeBinding this signal was published for
- `runtime_id` — complementary runtime-level routing

These fields are optional; existing signals without them are unaffected.

## Artifacts Changed

- `services/execution/lean_runtime/pending_signal_store.py` — `BINDING_QUEUE_KEY_PREFIX`, `binding_queue_key()`, auto-derive in `build_pending_signal_store()`
- `services/execution/lean_runtime/signal_consumer.py` — `binding_id` param, `_is_wrong_binding()` defense
- `services/execution/lean_runtime/paper_runtime.py` — explicit queue key derivation, passes `binding_id` to consumer
- `services/research/schema.json` — optional `binding_id` and `runtime_id` routing fields
- `services/execution/lean_runtime/test_signal_consumer.py` — 14 new tests (8 binding isolation + 6 store key resolution)

## Validation

```bash
python3 -m pytest services/execution/lean_runtime/test_signal_consumer.py -v
# Expected: 22 passed
```

---

# Paper Monitoring Session Stale Reaper — OPS-RTEL-003

Task: OPS-RTEL-003

## Scope

Paper fleet monitoring sessions are now owned by the runtime-manager fleet
reconciler instead of being inferred by `ended_at == null`.

- `PaperFleetReconciler` opens one `paper_runtime_monitoring` session for each
  spawned paper worker.
- Each session records binding/runtime identity, start/end timestamps,
  restart count, last heartbeat, stale threshold, and end reason.
- The reconciler reads telemetry runtime summaries from
  `PANTHEON_TELEMETRY_API_URL` or `PANTHEON_TELEMETRY_URL` and closes open
  sessions whose heartbeat is stale.
- A stale session terminates the tracked worker; the normal desired-state
  reconcile then starts a replacement worker with a new monitoring session.
- Persisted zombie sessions are also reaped on reconciler restart when their
  heartbeat evidence is stale.

## Configuration

Optional knobs:

```bash
RECONCILER_MONITORING_HEARTBEAT_STALE_SECONDS=90
PANTHEON_PAPER_RUNTIME_MONITORING_SESSION_STORE=/data/paper_runtime_monitoring_sessions.json
PANTHEON_PAPER_FLEET_RECONCILER_URL=http://paper-fleet-reconciler:8011
```

`PANTHEON_PAPER_RUNTIME_MONITORING_SESSION_STORE` persists the session list for
BFF/file-backed reads and restart reaping. If unset, sessions remain available
in the reconciler `/api/fleet/state` snapshot for the current process.

The BFF runtime-state board now joins this evidence as
`paper_runtime_monitoring` per row and reports the
`paper_runtime_monitoring` surface status separately from telemetry summaries.
The BFF never writes these sessions.

## Validation

Focused local validation for this task:

```bash
python3 -m pytest services/execution/runtime-manager/test_paper_fleet_reconciler.py -q
# Expected: 25 passed

python3 -m pytest services/control-plane/bff/test_pkt010_runtime_state_board_contract.py -q
# Expected: 4 passed
```

---

# BFF Runtime-State Truth Split and Closeout — OPS-RTEL-005

Task: OPS-RTEL-005

## Scope

The BFF runtime-state board now separates row-local runtime evidence from
board-level support surface health:

- Each runtime row includes `row_health`, covering only the runtime binding,
  telemetry summary row, and paper runtime monitoring row.
- `rollback_history` remains a board support surface under
  `meta.surfaces.rollback_history`; it does not make telemetry rows unhealthy
  when rollback history is degraded or unavailable.
- `meta.surfaces.runtime_state.support_surface_status` records the status of
  each support surface.
- `meta.surfaces.runtime_state.degraded_support_surfaces` names the surfaces
  causing the composed board to be degraded.

This lets operators see a healthy 15-runtime telemetry fleet while still
getting an explicit degraded banner for unavailable rollback history or other
supporting reads.

## Evidence

Closeout evidence is recorded in:

```text
support/evidence/OPS-RTEL-005/owner-closeout.md
```
