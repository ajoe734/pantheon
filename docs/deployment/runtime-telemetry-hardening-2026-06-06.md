# Runtime Telemetry Hardening Archive - 2026-06-06

## Status

This document archives the 2026-06-06 runtime telemetry incident and defines
the permanent repair plan. It is not itself an implementation closeout.

The live repair restored heartbeat visibility for the current paper runtime
roster, but the repair intentionally avoided enabling shared Redis signal
consumption for the temporary runtime containers. The permanent work must
replace that manual rescue with managed runtime lifecycle, durable telemetry
bootstrapping, session reconciliation, runtime-aware signal routing, and clearer
BFF health composition.

## Incident Summary

Observed user-facing state:

- 15 personas were deployed.
- 15 runtime bindings were active.
- all runtime stages were `paper`.
- broker state was `ok` / `paper_simulated`.
- runtime health rows showed `bridge`, `paper_runtime`, and `telemetry` as
  degraded.
- each last heartbeat was stale at about `2026-06-03T08:15:33Z` to
  `2026-06-03T08:15:34Z`, well beyond the 90 second staleness threshold.
- `recent_sse` was empty.
- `totalTrades` remained 0 or null, with PnL and fill-rate fields null.

Confirmed root causes:

1. The running Postgres instance did not have the `telemetry_events` table even
   though the repository already contains idempotent DDL in `scripts/db_migrate.sh`
   and `scripts/bootstrap.sh`.
2. The telemetry writer then failed after the table was restored because
   `build_postgres_write_fn()` passed RFC3339 `created_at` strings directly to
   asyncpg for a `TIMESTAMPTZ` parameter.
3. Active runtime bindings were registry truth only; there was no managed paper
   runtime fleet process keeping one worker alive per active paper binding.
4. Active paper monitoring sessions could remain `ended_at: null` after heartbeat
   flow stopped, creating zombie session truth.
5. The current LEAN runtime signal consumer can pop from a shared pending queue
   without runtime-specific filtering. Starting 15 real consumers against that
   queue risks cross-runtime signal consumption.
6. BFF runtime board meta can remain degraded because unrelated support surfaces,
   such as rollback history, are unavailable even when runtime telemetry rows
   are healthy.

## Live Repair State

Temporary live repair performed on 2026-06-06:

- applied the existing `telemetry_events` table DDL to the live Postgres instance.
- fixed the telemetry asyncpg timestamp binding bug in PR #1047.
- rebuilt and restarted `pantheon-telemetry-1`.
- started 15 paper runtime heartbeat containers with label
  `pantheon.live_repair=runtime-heartbeat-20260606`.
- set `SIGNAL_STORE_URL=` for those containers to avoid consuming from the shared
  Redis pending signal queue.

Live verification after the repair:

- BFF runtime-state reported 15 of 15 runtime rows with `bridge`,
  `paper_runtime`, and `telemetry` all `ok`.
- runtime heartbeat timestamps were current, with the oldest observed heartbeat
  at `2026-06-06T05:27:44Z` and the newest at `2026-06-06T05:28:04Z`.
- telemetry writer stats reached `ingested=274`, `written=274`, `failed=0`,
  `dlq=0`, `buffer=0`.
- Postgres `telemetry_events` reached 274 rows with latest `created_at` at
  `2026-06-06 05:28:04+00`.

This is still a temporary repair. It restores health telemetry, but it does not
yet provide managed runtime fleet lifecycle or safe runtime-specific signal
consumption.

## Permanent Target State

The permanent repair is complete only when all of the following are true:

- A running stack automatically applies or verifies telemetry schema before the
  telemetry service reports ready.
- Telemetry writer failures create actionable metrics and replayable DLQ evidence.
- Every active paper runtime binding is reconciled into exactly one managed paper
  runtime worker.
- Managed workers restart after failure and stop after binding retirement.
- Monitoring sessions are ended when their runtime heartbeat becomes stale.
- Runtime signal consumption is isolated by runtime or binding identity.
- BFF distinguishes runtime telemetry health from other support surface health.
- A full stack restart restores 15 active paper runtimes without manual
  `docker run` commands.

## Workstream A - Telemetry Durability

Permanent requirements:

- wire `scripts/db_migrate.sh` or equivalent idempotent DDL into stack startup,
  deployment, or service readiness.
- make telemetry readiness fail if required canonical tables are missing.
- add a transaction-rollback write probe or equivalent low-risk DB health probe.
- expose writer failure, DLQ, and latest durable event freshness metrics.
- add a DLQ replay command or operator action that replays only writer failures.
- keep schema and binding validation failures out of automatic replay.

Acceptance:

- a fresh Postgres volume starts with `telemetry_events` present before telemetry
  accepts runtime events.
- telemetry `/readyz` fails closed when the canonical table is missing.
- runtime heartbeat events persist to Postgres without DLQ entries.
- replaying writer-failure DLQ entries is idempotent by `event_id`.

## Workstream B - Paper Runtime Fleet Manager

Permanent requirements:

- add a managed `paper-runtime-fleet` reconciler service.
- read active `paper` runtime bindings from runtime-manager.
- maintain exactly one worker per active binding.
- pass complete runtime context into each worker, including runtime id, binding
  id, persona id, strategy id, capital pool id, plan id, artifact id/version,
  and engine bridge repo/path/commit.
- auto-restart crashed workers and record restart reason.
- stop workers whose bindings are retired, paused, or no longer active.
- expose expected, running, stale, restarting, and stopped counts.

Safety constraint:

- until Workstream D is complete, fleet workers must not consume from the shared
  Redis signal queue. Heartbeat-only or monitoring-only workers may run with a
  disabled signal store.

Acceptance:

- stack restart creates workers for all active paper bindings without manual
  container creation.
- killing one worker results in automatic restart and heartbeat recovery.
- retiring a binding stops its worker and prevents further heartbeat writes.

## Workstream C - Monitoring Session Reconciliation

Permanent requirements:

- end active `paper_runtime_monitoring` sessions when their heartbeat exceeds
  the configured stale threshold for more than the tolerated grace window.
- record `ended_at`, terminal status, and reason such as `heartbeat_stale` or
  `runtime_process_missing`.
- create a new monitoring session when fleet restarts a worker.
- prevent BFF from treating `ended_at: null` as sufficient proof of live runtime.

Acceptance:

- stale active sessions are ended by an automated reaper.
- restarted runtimes get new session records.
- BFF exposes session staleness explicitly.

## Workstream D - Runtime-Aware Signal Isolation

Permanent requirements:

- include `runtime_id`, `runtime_binding_id`, or an equivalent routable identity
  in each signal envelope.
- prevent a runtime from consuming signals for another runtime, persona, or
  capital pool.
- replace blind shared-list `LPOP` consumption with either per-runtime queues or
  an atomic claim/ack/nack protocol with binding validation.
- add tests with multiple consumers proving no cross-runtime consumption.
- only enable real paper signal consumption after isolation passes.

Acceptance:

- 15 runtime consumers can run concurrently without cross-consuming signals.
- a signal addressed to runtime A cannot be claimed by runtime B.
- failed claims are requeued or dead-lettered with diagnostic reason.

## Workstream E - BFF Runtime-State Truth

Permanent requirements:

- split row-level runtime telemetry health from board-level supporting surface
  health.
- report rollback-history degradation separately from runtime heartbeat health.
- keep `runtime_state` meta honest without hiding row-level recovery.
- expose enough fields for operators to see binding status, process status,
  heartbeat freshness, telemetry durability, and support-surface degradation.

Acceptance:

- runtime rows can show 15 of 15 healthy even if rollback history is unavailable.
- board meta explains which support surface is degraded.
- tests cover telemetry-ok plus rollback-unavailable as a distinct state.

## Transition Off Temporary Repair

The temporary containers labeled
`pantheon.live_repair=runtime-heartbeat-20260606` may be removed only after the
managed fleet service passes the stack restart and kill-one-worker acceptance
tests. Before removal, verify that the managed fleet emits fresh heartbeat
events and durable Postgres rows for the same 15 runtime bindings.

Do not enable shared Redis signal consumption for the temporary containers.
Real signal consumption remains blocked until runtime-aware signal isolation is
implemented and verified.
