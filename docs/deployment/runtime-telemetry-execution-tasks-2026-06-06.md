# Runtime Telemetry Hardening Execution Tasks - 2026-06-06

## Source Archive

Primary archive:

- `docs/deployment/runtime-telemetry-hardening-2026-06-06.md`

Related merged repair:

- PR #1047
- commit `9a8585b14b49627240eff50cd277ac7efd42cbb8`
- merge commit `eb6b0295ca9e555b23e715713642974d6baf7472`

## Task DAG

| Task | Owner | Reviewer | Depends on | Purpose |
| --- | --- | --- | --- | --- |
| `OPS-RTEL-001` | Codex | Claude | - | Make telemetry schema, readiness, metrics, and DLQ replay durable. |
| `OPS-RTEL-002` | Claude | Codex | `OPS-RTEL-001` | Add managed paper runtime fleet reconciliation. |
| `OPS-RTEL-003` | Codex2 | Claude2 | `OPS-RTEL-002` | Reap zombie paper monitoring sessions and align restarted sessions. |
| `OPS-RTEL-004` | Claude2 | Codex2 | `OPS-RTEL-002` | Isolate signal consumption by runtime or binding identity. |
| `OPS-RTEL-005` | Codex | Claude | `OPS-RTEL-001`, `OPS-RTEL-002`, `OPS-RTEL-003`, `OPS-RTEL-004` | Split BFF runtime telemetry truth from unrelated support-surface degradation and close the hardening wave. |

## OPS-RTEL-001 - Telemetry Durability Bootstrap

Scope:

- `scripts/db_migrate.sh`
- `scripts/bootstrap.sh`
- `services/telemetry/main.py`
- `services/telemetry/ingest_svc.py`
- telemetry tests under `services/telemetry/`

Implementation requirements:

- ensure a fresh stack creates or verifies `telemetry_events` before telemetry
  reports ready.
- add readiness failure for missing canonical telemetry tables.
- add a low-risk DB write probe, preferably transaction rollback.
- expose writer failure, DLQ count, and durable event freshness in stats or
  health output.
- implement replay for DLQ writer failures only.

Acceptance:

- fresh Postgres volume plus stack bootstrap creates `telemetry_events`.
- telemetry `/readyz` fails when the canonical table is missing.
- heartbeat ingest writes to Postgres with `writer.total_failed=0` and
  `writer.total_dlq=0`.
- DLQ replay is idempotent by `event_id`.

## OPS-RTEL-002 - Paper Runtime Fleet Reconciler

Scope:

- new fleet service under `services/execution/` or an equivalent runtime-manager
  owned module.
- `docker-compose.yml` service wiring.
- runtime-manager client integration.
- LEAN runtime bootstrap environment contract.

Implementation requirements:

- reconcile active paper runtime bindings into one worker per binding.
- pass full runtime context to every worker.
- restart crashed workers and record restart reason.
- stop workers when bindings are retired or paused.
- expose fleet health counts.
- keep workers heartbeat-only or signal-store-disabled until `OPS-RTEL-004`
  lands.

Acceptance:

- stack restart starts workers for all active paper bindings without manual
  `docker run`.
- killing one worker causes automatic restart and fresh heartbeat recovery.
- retired binding stops its worker.
- no worker consumes from the shared Redis signal queue before signal isolation.

## OPS-RTEL-003 - Monitoring Session Stale Reaper

Scope:

- runtime-manager session model or session store.
- BFF runtime-state read model if it projects monitoring sessions.
- tests for stale monitoring sessions.

Implementation requirements:

- end active `paper_runtime_monitoring` sessions after heartbeat staleness grace.
- record terminal reason and `ended_at`.
- create fresh session records on fleet restart.
- prevent `ended_at: null` from being treated as runtime liveness proof.

Acceptance:

- stale sessions are automatically ended.
- restarted workers create fresh sessions.
- BFF surfaces session staleness and terminal reason.

## OPS-RTEL-004 - Runtime-Aware Signal Isolation

Scope:

- `services/execution/lean_runtime/signal_consumer.py`
- signal store implementation used by paper runtime.
- signal envelope schema and tests.

Implementation requirements:

- route or claim signals by runtime or binding identity.
- remove blind shared-list consumption for multi-runtime paper execution.
- add ack/nack/requeue or per-runtime queues.
- reject mismatched runtime, persona, or capital-pool signals.
- add concurrency tests with 15 consumers.

Acceptance:

- multiple runtime consumers cannot consume each other's signals.
- mismatched signals are rejected or dead-lettered with reason.
- real paper signal consumption can be enabled only after this task passes.

## OPS-RTEL-005 - BFF Runtime-State Truth Split and Closeout

Scope:

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/read_store.py`
- BFF runtime-state tests.
- final operational evidence under `support/evidence/`.

Implementation requirements:

- separate runtime telemetry row health from board support-surface health.
- report rollback-history unavailable separately.
- include binding, process, heartbeat, telemetry durability, and support-surface
  status in operator-facing payloads.
- collect final evidence after `OPS-RTEL-001` through `OPS-RTEL-004` land.

Acceptance:

- BFF can report 15 of 15 runtime telemetry rows healthy while rollback history
  remains unavailable.
- board meta names the degraded support surface.
- final evidence proves stack restart, kill-one-worker recovery, binding retire
  stop, telemetry durability, and signal isolation.
