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
Each batch (`RECLASSIFY_STALE_OPEN_SQL`) updates the
`journeys` row and its linked `loop_runs` row together (loop `status` = terminal status,
`activePeriod.end` = journey `last_occurred_at`) and commits. An empty sweep runs only this one bounded statement; there is no separate repair scan. A sweep failure is logged and retried next interval; it
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

## Historical paper journey cause repair (`TRADE-JOURNEY-HISTORICAL-CAUSE-REPAIR-20261121`)

Historical `order_submission | failed` (`order_rejection` / `order_rejection_simulated`) and
`reconciliation | failed` (`reconciliation_failed`) rows projected before
`TRADE-JOURNEY-FAILURE-CAUSE-20261121` are repaired in bounded batches from immutable source
evidence (`public.telemetry_events` and `reconciliation_drift.drift_evaluations`) via
`scripts/repair_paper_journey_causes.py` (or
`python3 -m services.trade_journey.lifecycle_projector repair-causes`).

### Invariants and scope

* **Scoped joins**: Joins strictly by `(tenant_id, environment, journey_id)` and exact
  `source_event_id`, `reconciliation_evaluation_id` (using the owner store key
  `'tenant-record-' + sha256(f"{tenant_id}\0{evaluation_id}")`), `source_fill_event_id` /
  `fill_id`, and `causal_parent_id` (`position_snapshot`).
* **Immutability**: Never modifies `public.telemetry_events`,
  `public.trade_journey_action_ledger`, `reconciliation_drift.*`, ledger tables, or paper
  broker orders, and never replays trading side effects.
* **Classification buckets**:
  * `recoverable_refusals`: `order_submission | failed` rows whose source event yields a
    concrete non-expression rejection reason (`insufficient_cash`, `sell_without_holdings`,
    `taiwan_broker_error`, etc.) and threshold/submission details (including the 2317 buy
    refusal `tj_79726064-4cc2-5510-8e11-7f5e59ff89bd`, event
    `91d55953-b575-4fe8-8d39-6e0cade58d59` with `required=100034.3461`,
    `available=100000.0`, `submitted_to_broker=false`, `order_status="not_submitted"`).
  * `health_only_evaluations`: `reconciliation | failed` rows whose joined evaluation in
    `reconciliation_drift.drift_evaluations` contains only runtime-health or drift checks
    with no proven trade-specific booking discrepancy. On fill journeys (including the 2317
    sell `tj_57418ba7-ef37-5015-9b5e-b6423872da89`, recon event
    `48c946c8-39d1-52bd-b1a1-ce72dc7f6e14`, fill event
    `000600fb-6496-47d9-a550-1fa23449c1e2`, snapshot
    `8d6761c5-5ae8-5799-87bb-10302186595a`, evaluation
    `rdeval-sched-scheduled-tenant-dev-2026-11-16T-rb-7f3c1be55a224fdaa36bb-a39cdf74492b52bb`),
    restores the recorded fill (`1 @ 218.5`) and its health warning as unverified accounting
    (`reason_code="reconciliation_evidence_incomplete"`, `classification="unverified_evidence"`,
    `accounting_variance=false`, `comparison_completed=false`) until actual trade-specific
    evidence establishes a result. A subsequent healthy tick alone proves only runtime health.
  * `proven_variances`: `reconciliation | failed` rows where complete trade-specific booking
    evidence shows a discrepancy beyond `1e-6`, preserving `expected`, `actual`,
    `difference`, `delta`, and the variance `reason_code`.
  * `irrecoverable_records`: rows whose source event or evaluation record is absent (such as
    `2026-10-07` reconciliation rows prior to `reconciliation_drift.drift_evaluations`
    persistence), preserving `reason_code="reason_not_recorded"` and `missing_evidence`.
* **Concurrency and rollback safety**: Every stage `UPDATE` is guarded by the expected stage
  `projection_revision`, `fingerprint`, `contract_fields`, and parent journey
  `projection_revision`. Before modifying a row, its scoped before-image is appended to
  `/tmp/pantheon-journey-cause-repair/paper_journey_cause_repair_before_image.jsonl` (capped
  at `16 MiB` / `25,000` records, 7-day TTL auto-prune).

### Operator procedure (after version is hosted on dev)

1. **Dry-run inventory (read-only transaction)**:
   ```bash
   python3 scripts/repair_paper_journey_causes.py \
     --tenant-id tenant-dev --environment paper \
     --batch-size 500 --max-batches 20 --dry-run \
     --output /tmp/pantheon-journey-cause-repair/dry-run-report.json
   ```
2. **Bounded resumable apply**:
   ```bash
   python3 scripts/repair_paper_journey_causes.py \
     --tenant-id tenant-dev --environment paper \
     --batch-size 500 --max-batches 20 --apply --resume \
     --output /tmp/pantheon-journey-cause-repair/apply-report.json
   ```
3. **Verify repeat run changes zero already-repaired rows**:
   ```bash
   python3 scripts/repair_paper_journey_causes.py \
     --tenant-id tenant-dev --environment paper \
     --batch-size 500 --max-batches 20 --apply \
     --output /tmp/pantheon-journey-cause-repair/repeat-report.json
   ```
   Confirm `updated_rows == 0` and `needs_update_rows == 0`.
4. **Rollback (if needed) or cleanup temporary before-image evidence**:
   ```bash
   # Rollback (protects any revision subsequently advanced by the live projector):
   python3 scripts/repair_paper_journey_causes.py \
     --tenant-id tenant-dev --environment paper --rollback

   # Clean up temporary before-image and cursor files after verification:
   python3 scripts/repair_paper_journey_causes.py --cleanup-temp
   ```

