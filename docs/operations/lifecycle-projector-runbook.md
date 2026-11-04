# Lifecycle projection schema bootstrap

The projector opens `ProjectionStore` with `bootstrap=False`. An empty database
therefore requires the existing versioned SQL migration before runtime startup.
Schema DDL belongs to the migration connection; runtime receives only schema
USAGE and SELECT/INSERT/UPDATE/DELETE on the seven projection tables.

Dev `root` and `bff` deployment paths run a bounded, one-shot bootstrap from the
sealed candidate projector image before starting that runtime. The existing
PostgreSQL service must be healthy. Its resolved Compose credentials pass through
stdin to the migration CLI, so `.env` overrides are honored without copying
credentials into the live projector or writing a config artifact. Failure enters
the existing deployment compensation path. Existing readiness gates still apply.

For an isolated database, use the same entry point explicitly before starting a
projector outside the dev deployment script:

```bash
export LIFECYCLE_PROJECTOR_PROJECTION_DSN='<runtime connection>'
export LIFECYCLE_PROJECTOR_PROJECTION_SCHEMA=trade_journey_projection
python -m scripts.lifecycle_projector_migrate --bootstrap-only \
  --dsn '<migration connection>' --schema "$LIFECYCLE_PROJECTOR_PROJECTION_SCHEMA" \
  --reconcile-runtime-role
```

Both connections must resolve to the same database/server, with distinct users.
The schema must match the runtime configuration. The sealed dev bootstrap opts
into `--reconcile-runtime-role` for the known `trade_journey_projection` schema.
This transfers directly runtime-owned projection tables and schema to the separate
migration identity, then revokes that runtime's direct schema CREATE and table
TRIGGER grants. The final DDL rejection remains enforced. Without this explicit
option, ownership or CREATE still causes bootstrap to fail.

Reconciliation admits only the seven known ordinary tables and their indexes,
owned by the runtime or migration role. PUBLIC/inherited CREATE or TRIGGER,
inherited administrative authority, other owners, unknown relations, custom
routines/types/triggers, and elevated runtime roles fail closed. The migration
identity must already have the authority to transfer ownership; bootstrap never
grants administrative membership. Resolve refused layouts through their existing
owner instead of expanding this migration or retrying deployment blindly.

Other schemas, global role attributes/memberships, and unrelated grants remain
unchanged. The normal migration grants only projection-table DML. Source-default
`pantheon_app` authority in `public` is intentionally outside this repair.

The migration and grants commit together. Reruns preserve receipts, owner data,
controller revisions and live checkpoints. A DDL/grant error rolls the transaction
back and prevents candidate startup. Bootstrap creates no controller row and
cannot certify accepted-live readiness; normal source polling and existing gates
provide that evidence. Backfill remains a separate mode requiring controller and
snapshot arguments.

Validation for DEV-PROJECTION-ROLE-UPGRADE-20261003 uses disposable PostgreSQL,
the actual init shell, resolved Compose defaults and the bootstrap shell/CLI
boundary, with Docker candidate transport replaced by local process execution.
It covers existing ownership/grants, retained live data, restart, unsafe layouts,
and DDL/grant rollback. Artifact seal ordering and compensation have separate
deployment contract tests. These are local source proofs, not hosted acceptance.
Only the existing release coordinator may consume the merged source for a bounded
upgrade/deploy; the earlier failed root run is not an accepted candidate.

## Stale-open journey reclassification (`no_order` / `unreconciled`)

Rollup rule (`JourneyMaterializer._rollup` / `deadline_status`):

| Condition | Status |
| --- | --- |
| `order_submission` skipped | `no_order` |
| open, no `reconciliation` stage, last event older than the deadline, no `order_submission` stage | `no_order` |
| open, no `reconciliation` stage, last event older than the deadline, `order_submission` present | `unreconciled` |
| within the deadline | `open` (existing stalled flag unchanged) |

Deadline: `PANTHEON_TRADE_JOURNEY_RECONCILIATION_DEADLINE_SECONDS`, default 72h. Both statuses are in `TERMINAL_STATUSES`, so the BFF never flags them stalled; `unreconciled` is never presented as `completed`. The runtime-binding-ended signal is not carried on projection rows, so the deadline is the only criterion. Pure replays pass no `as_of` and stay deterministic; only the live projector applies the deadline.

Existing rows: migration `003_reclassify_stale_open_journeys.sql` only adds a partial
index on open journeys (no routines, no row writes, so it never blocks readiness).
Reclassification is **automatic**: once the projector is ready and polling, its run loop
(`StaleOpenSweeper`, next to `record_poll`) applies at most one bounded batch per interval
in its own short transaction on the projection DSN (role `pantheon_app`). No manual step.
Each batch (`RECLASSIFY_STALE_OPEN_SQL` + `REPAIR_ACTIVE_LOOP_RUNS_SQL`) updates the
`journeys` row and its linked `loop_runs` row together (loop `status` = terminal status,
`activePeriod.end` = journey `last_occurred_at`), then repairs loop_runs still `active`
beside an already-terminal journey. A sweep failure is logged and retried next interval; it
never fails the tick, changes readiness, or pauses source polling. A late event re-reduces
the journey, so a late `reconciliation` moves it to its normal `completed` status.

| Env | Default | Meaning |
| --- | --- | --- |
| `LIFECYCLE_PROJECTOR_RECLASSIFY_ENABLED` | `1` | `0` disables the sweep |
| `LIFECYCLE_PROJECTOR_RECLASSIFY_INTERVAL_SECONDS` | `10` | one batch per interval |
| `LIFECYCLE_PROJECTOR_RECLASSIFY_BATCH_SIZE` | `1000` | keep near 1000: hosted statement timeout is ~10s |

Defaults drain about 360k rows/hour, so the ~150k backlog clears in well under an hour.
An empty sweep is one index probe on `idx_journeys_open_last_occurred` plus a scan for
active loop_runs beside terminal journeys.

Expected hosted result after deployment (from the 2026-11-04 baseline of 147,149 open):
`no_order` 36,124 and `unreconciled` 111,025, `open` ~0, and `loop_runs` with no `active`
row beside a terminal journey.

Verify after deployment: `SELECT status, count(*) FROM trade_journey_projection.journeys GROUP BY 1` and the same on `trade_journey_projection.loop_runs` (statuses must match).
