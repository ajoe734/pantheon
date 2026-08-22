# Pantheon Functional Closure SD — 2026-08-22

This software design turns `CURRENT_GAP_2026-08-22.md` and
`SA_IMPLEMENTATION_PLAN_2026-08-22.md` into file-level implementation
contracts. It is deliberately functional-first. Security hardening is not part
of this closure program except where existing authentication is required to run
a real hosted journey.

The design reuses the current projector, RuntimeBinding state machine, fleet
reconciler, BFF projection reader, loop inventory, browser-auth helpers, and
canonical tasks. It must not introduce parallel control planes or supersede
nonterminal tasks merely because this document gives them more precise scope.

## 1. Baseline and invariants

Implementation starts from Pantheon `origin/dev`
`8cb621e5bac74225d6b7a1a94d4650013aed470d` and execute-plans `origin/dev`
`693d8612218e5ec6620c80ab7a16d3429e842f6c`. Before a worker edits either
repository it must refresh the exact SHA and record any change in its task
evidence.

The following invariants are mandatory:

1. `public.telemetry_events` remains canonical source data. A deployment may
   not truncate it, rebuild it from a projection, or silently replace missing
   history with fixture data.
2. Dev Source provider egress remains manual and bounded: one operator/test
   request causes one bounded pull. No timer, daemon, retry loop, or startup
   hook may continuously pull an external provider.
3. Lifecycle relational projection is derived and replayable. It never becomes
   the source of canonical telemetry.
4. RuntimeBinding remains the single execution-session state authority. No
   separate paper-session status enum or state store is allowed.
5. Static loop catalog data describes stable ownership and contracts only.
   Current health is accepted only from runtime evidence.
6. Fixture browser tests can prove component behavior but cannot close a hosted
   product journey.
7. Compatibility code is removed only after its replacement passes caller
   inventory, live-path tests, and rollback checks.

## 2. Change map

| Design unit | GAP | Primary existing owner | Result |
|---|---|---|---|
| SD-DATA-01 | G-01 | nonprod deploy script | canonical telemetry cannot be pruned |
| SD-DATA-02 | G-01/G-02 | baseline evidence tooling | explicit history disposition |
| SD-LIFE-01 | G-02 | Lifecycle projector/store | bounded relational backfill and shadow |
| SD-LIFE-02 | G-02 | BFF projection reader | relational cutover and JSON retirement |
| SD-PAPER-01 | G-03 | RuntimeBinding/fleet/producer | stale input pauses a bounded session |
| SD-LOOP-01 | G-04 | loop inventory | 12 runtime-backed loop truth records |
| SD-MGMT-01 | G-05 | execute-plans E2E | real hosted Management journey |
| SD-AGORA-01 | G-06 | execute-plans Agora/E2E | real hosted Agora journey |
| SD-CLEAN-01 | G-07 | consolidation tasks | evidence-led duplicate/dead-code removal |
| SD-REL-01 | G-07 | hosted acceptance | exact FE/BFF pair acceptance |

## 3. SD-DATA-01 — Safe nonproduction telemetry handling

### 3.1 Existing code to modify

- `scripts/deploy_nonprod_vm.sh`, function
  `prune_dev_management_ai_telemetry_for_disk` and its call site.
- `scripts/test_management_ai_postgres_bootstrap_contract.py`.
- A focused new shell/SQL contract test may be added under `scripts/`, but a
  second deployment cleanup script must not be added.

### 3.2 Target behavior

The deployment flag `PANTHEON_DEV_POSTGRES_TELEMETRY_PRUNE` may retain its name
for compatibility, but its target resolution must be explicit:

```text
requested target schema
  -> validate identifier
  -> require target != public
  -> discover telemetry-like tables in target only
  -> truncate the allow-listed derived tables
  -> verify public.telemetry_events row count and minimum timestamp unchanged
```

The SQL must remove `public` from namespace matching. It must not use a fallback
expression such as `IN (target_schema, 'public')`. The safe allow-list is the
configured Management AI derived schema and known derived tables within that
schema. A missing derived table is a no-op; an invalid schema or an attempt to
target `public` is a hard deployment failure before mutation.

Capture these pre/post values in the deployment artifact:

```json
{
  "canonical_table": "public.telemetry_events",
  "canonical_row_count_before": 0,
  "canonical_row_count_after": 0,
  "canonical_min_created_at_before": null,
  "canonical_min_created_at_after": null,
  "derived_schema": "management_ai_projection",
  "derived_tables_pruned": [],
  "result": "preserved"
}
```

Exact equality of canonical pre/post values is required. This is a preservation
sentinel, not an assertion that history is complete.

### 3.3 Tests

Positive tests:

- a derived telemetry table is emptied when pruning is enabled;
- missing derived tables do not fail deployment;
- pruning disabled performs no mutation;
- existing bootstrap behavior remains valid.

Negative tests:

- `target_schema=public` fails before SQL mutation;
- a malicious/invalid identifier fails validation;
- a public table with a telemetry-like name is not selected;
- canonical row-count or minimum-timestamp drift makes the deploy fail;
- the generated SQL contains no `TRUNCATE public.telemetry_events` path.

### 3.4 Acceptance and rollback

Acceptance requires two consecutive deploy dry-runs and one real dev deploy
with identical canonical sentinels. Rollback is the previous deploy artifact;
it must be used with pruning disabled. Rollback must never restore source truth
from the Lifecycle JSON projection.

## 4. SD-DATA-02 — Canonical telemetry baseline disposition

The observed repopulation beginning around `2026-08-22 05:21:28+00` does not
prove older history exists. Create a versioned baseline evidence artifact under
the existing operations evidence location, not a new runtime database.

Required schema:

```json
{
  "captured_at": "RFC3339",
  "environment": "dev",
  "deployment_sha": "full SHA",
  "source_table": "public.telemetry_events",
  "row_count": 0,
  "min_created_at": null,
  "max_created_at": null,
  "source_high_watermark": null,
  "known_history_start": null,
  "history_disposition": "complete|partial|irrecoverable|unknown",
  "recovery_source": null,
  "query_sha256": "sha256",
  "operator_note": ""
}
```

`history_disposition=complete` is allowed only when an authoritative backup or
source ledger proves it. `partial`, `irrecoverable`, and `unknown` remain honest
operational states and do not block new-event functional validation. The tool
must never synthesize missing source events from `lifecycle_projection.json`.

Tests must reject an unrecognized disposition, a truncated SHA, missing query
hash, or a claim of `complete` without a recovery-source proof reference.

## 5. SD-LIFE-01 — Relational Lifecycle backfill and shadow

### 5.1 Reuse boundary

Extend these existing components:

- `services/trade_journey/lifecycle_projector.py`, including
  `RelationalLifecycleProjector` and the existing writer-backend configuration;
- `services/trade_journey/projection_store.py`, `ProjectionStore`;
- `services/trade_journey/migrations/001_create_trade_journey_projection_schema.sql`;
- `scripts/lifecycle_projector_migrate.py` and
  `scripts/lifecycle_projector_parity.py`;
- existing Lifecycle unit, compose, readiness, and hosted readback tests.

Do not create a second projector, cursor service, projection schema, or event
receipt ledger.

### 5.2 Existing relational model

The seven current tables remain authoritative for the derived projection:

| Table | Responsibility | Idempotency key |
|---|---|---|
| `controller` | checkpoint, watermark, mode and readiness | controller/tenant/environment |
| `event_receipts` | source-event disposition | event_id, plus unique ingested_seq |
| `identity_links` | identifier-to-journey resolution | tenant/environment/type/value |
| `journeys` | current journey summary | tenant/environment/journey_id |
| `journey_stages` | ordered stage evidence | tenant/environment/journey/event/stage |
| `loop_runs` | loop-run projection | tenant/environment/loop_run_id |
| `quarantine` | unresolved malformed/conflicting input | event_id/ingested_seq |

Schema changes are additive migrations only. Migration 001 is not rewritten
after it has been applied. Any required index or field is introduced by a new,
idempotent numbered migration.

### 5.3 Writer modes and transitions

The current `disabled` and `shadow` writer modes are retained. Operational
progress uses controller `mode` and release configuration rather than adding a
new writer implementation:

```text
disabled
   |
   v
shadow + controller.mode=backfill
   |
   v  checkpoint reaches captured high watermark
shadow + controller.mode=live
   |
   v  parity and freshness gates pass
relational reader enabled (writer remains shadow-named compatibility value)
```

The compatibility value `shadow` may be renamed only after all configuration
callers are migrated. Its name must not trigger two writes to two relational
stores; it means the one existing relational writer runs alongside the legacy
JSON writer during migration.

### 5.4 Transaction contract

For each bounded source batch:

1. Read canonical events strictly after `controller.checkpoint_seq`, capped by
   configured batch size and the run's high watermark.
2. Claim/write `event_receipts` using event ID, ingested sequence, and
   fingerprint.
3. Apply identity, journey, stage, and loop-run mutations in the same database
   transaction.
4. Quarantine a structurally invalid/conflicting event without advancing it as
   successfully applied.
5. Advance checkpoint, source watermark, counts, timestamps, revision, and
   deployment SHA atomically with the batch.
6. Commit once. A retry observes receipts and is idempotent.

A process crash before commit leaves no partial batch. A retry after commit
must not duplicate stages or increment journey revisions for identical input.
The run is bounded by both batch size and captured high watermark; new source
events are handled by later normal polls.

### 5.5 Backfill command contract

`scripts/lifecycle_projector_migrate.py` must support an inspectable sequence:

```text
plan  -> print schema, source range, target controller and estimated batches
apply -> run numbered migrations and bounded replay to captured watermark
check -> report counts, checkpoint, quarantine and parity artifact
```

Every command requires tenant, environment, projection DSN/schema, source
range, batch size, deployment SHA, and evidence output path. `apply` without a
captured high watermark is rejected. Re-running the same command is safe.

### 5.6 Parity contract

`scripts/lifecycle_projector_parity.py` compares canonical source-derived
expectations, legacy JSON output, and relational output by tenant/environment.
It reports at least journey IDs, stage sequences, terminal state, identity
links, loop-run association, checkpoint/watermark, unresolved quarantine, and
freshness. It emits machine-readable JSON plus a human summary.

Cutover gates:

- checkpoint equals the captured watermark;
- backlog at the gate is zero;
- unresolved quarantine is zero or explicitly dispositioned with references;
- no missing/extra journey IDs in the selected window;
- stage order and terminal status match;
- two consecutive live shadow windows pass;
- restart/replay produces no projection diff.

Negative tests cover duplicate IDs with same fingerprint, conflicting
fingerprint, identity collision, out-of-order arrival, crash/retry, malformed
events, tenant leakage, and source growth during a bounded backfill.

## 6. SD-LIFE-02 — BFF reader cutover and JSON retirement

### 6.1 Reader switch

Use `services/control-plane/bff/trade_journey_projection_store.py` and the
existing `PANTHEON_BFF_TRADE_JOURNEY_READER_BACKEND` configuration. Do not add
new BFF routes or response shapes.

Release states:

| State | Writer | BFF reader | Legacy JSON |
|---|---|---|---|
| pre-migration | JSON; relational disabled | JSON | writable |
| shadow | JSON + existing relational writer | JSON | writable |
| candidate | JSON + relational | relational in candidate deployment | writable rollback source |
| accepted | relational normal path | relational | read-only, retained briefly |
| retired | relational | relational | archived then removed |

The candidate must fail closed if relational readiness is false; it must not
silently fall back to JSON and claim relational acceptance. Rollback explicitly
restores the prior reader configuration and release artifact.

### 6.2 BFF compatibility tests

Run the same list/detail/readiness contract suite against JSON and relational
readers, then compare normalized responses. Cover pagination, filters,
tenant/environment boundaries, unknown IDs, nonterminal and terminal journeys,
loop-run readback, stale controller, backlog, quarantine, and unavailable DB.

### 6.3 Bounded storage and retirement

After relational hosted acceptance:

1. stop JSON writes;
2. prove no reader/caller opens the JSON path;
3. archive one rollback artifact with SHA256, byte size, source deployment SHA,
   checkpoint, and retention expiry;
4. restart the projector and prove relational-only readiness/readback;
5. remove generations and controller-state files from the live volume;
6. remove legacy code/config only in consolidation tasks.

The relational store requires explicit retention/compaction for receipts and
completed journey evidence, but retention must not delete canonical telemetry.
The 18 GiB JSON directory is not deleted before steps 1–4 pass.

## 7. SD-PAPER-01 — Bounded paper session on manual Source data

### 7.1 State design

Reuse `RuntimeBindingStatus` exactly:

```text
active --stale/invalid source--> pending_pause --> paused
paused --new admitted snapshot--> active
active/paused ------------------> retired or failed (existing rules)
```

Do not add `paused_stale_input`, a paper-only binding table, or an always-on
Source poller. Pause cause belongs in RuntimeBinding metadata, not status.

Structured metadata patch:

```json
{
  "session_admission": {
    "reason_code": "market_input_stale",
    "source_snapshot_id": "snapshot-id",
    "source_event_time": "RFC3339",
    "observed_at": "RFC3339",
    "max_age_seconds": 86400,
    "pause_command_ref": "command-or-event-ref",
    "resume_snapshot_id": null,
    "resumed_at": null
  }
}
```

Extend the existing RuntimeBinding transition operation with an atomic,
validated metadata patch if needed. Do not let the signal producer edit the
binding JSON/store directly.

### 7.2 Single admission rule

Extract the current snapshot checks from
`services/execution/lean_runtime/paper_signal_producer.py` into one shared,
side-effect-free admission function returning:

```text
admitted(snapshot_id, event_time, age_seconds)
or rejected(reason_code, detail, snapshot_id, event_time, age_seconds)
```

The producer continues to call it as a final defense. The existing
`services/execution/runtime-manager/paper_fleet_reconciler.py` calls the same
function before starting/retaining a worker and requests the existing
RuntimeBinding transition. This preserves one policy implementation and one
state authority.

On rejection, the reconciler records the metadata, transitions
`active -> pending_pause -> paused`, and stops the worker through its existing
paused-binding behavior. Repeated reconciliation is idempotent. It must not
emit orders, manufacture recent closes, or trigger a provider pull.

### 7.3 Resume rule

A manual Source pull publishes one new canonical snapshot. At the next bounded
reconcile, resume is allowed only if:

- the snapshot passes the same admission function;
- its ID differs from the paused snapshot ID;
- event time is later than the paused snapshot event time;
- binding/artifact/capital-pool ownership is unchanged; and
- no operator or terminal pause reason supersedes stale-input pause.

The reconciler atomically records `resume_snapshot_id` and `resumed_at`, then
uses the existing `paused -> active` transition. It starts at most one worker.
It does not keep pulling for another snapshot.

### 7.4 Tests

Positive tests:

- fresh snapshot retains/starts exactly one active worker;
- stale input causes exactly one pause sequence and worker stop;
- a newer manually fetched snapshot resumes the same binding once;
- producer and reconciler return identical admission decisions;
- restart while paused stays paused and starts no worker.

Negative tests:

- the same stale snapshot cannot resume;
- a future, missing, malformed, or wrong-scope snapshot cannot resume;
- repeated stale reconciles do not create repeated commands/transitions;
- paused/retired/failed bindings cannot produce or fill orders;
- an operator pause is not auto-resumed as stale-input recovery;
- no test observes recurring external provider traffic.

## 8. SD-LOOP-01 — Twelve-loop runtime truth

### 8.1 Stable catalog contract

Reduce `docs/deployment/loop-catalog.registry.json` to stable fields used by the
runtime projection:

```json
{
  "loop_id": "loop-01",
  "classification": "product-loop",
  "owner": "component-owner",
  "controller_name": "runtime-controller-name",
  "desired_state_query": "query/adapter identifier",
  "actual_state_query": "query/adapter identifier",
  "restart_behavior": "idempotent|resume|reconcile",
  "liveness_metric": "metric identifier",
  "idempotency_contract": "stable contract reference"
}
```

Remove current maturity, current status, execution-task lists, historical
evidence, and implementation prose from the runtime truth calculation. A
separate overlay may describe non-product support controllers but must not be
counted among the twelve product loops.

### 8.2 Runtime observation contract

Each of the twelve records returned by `loop_inventory.py` must include:

```json
{
  "loop_id": "loop-01",
  "tenant_id": "tenant",
  "environment": "dev",
  "deployment_sha": "full SHA",
  "controller_name": "name",
  "controller_status": "running|degraded|stopped|unknown",
  "last_heartbeat_at": "RFC3339",
  "desired_state_present": true,
  "downstream_actual_state": "observed value",
  "last_trigger_at": "RFC3339|null",
  "last_success_at": "RFC3339|null",
  "last_failure_at": "RFC3339|null",
  "terminal_output_ref": "reference|null",
  "next_receipt_ref": "reference|null",
  "truth_level": "runtime|partial|contract_only|unavailable",
  "runtime_evidence_refs": [],
  "current_record_accepted": false,
  "rejection_reasons": []
}
```

`current_record_accepted=true` requires: the catalog has a stable contract;
controller name matches; deployment SHA is exact; heartbeat is fresh for that
controller's cadence; desired state is observable; downstream actual state is
observable; trigger/success/failure evidence is internally consistent; and the
terminal output or next receipt exists where the contract requires it.

Static catalog status can never satisfy any current-runtime predicate. A fresh
controller with missing downstream evidence remains unaccepted. A complete
runtime observation with a missing stable catalog contract also remains
unaccepted.

### 8.3 PR #5122 correction

Amend, do not duplicate, the existing loop-truth task/PR. Preserve its removal
of static maturity output, but remove acceptance dependence on static
controller status. Retain stable controller-name validation. Add adapters for
Loops 4–12 instead of marking them `not_implemented` in a current projection.

Tests cover exactly twelve product records, overlay exclusion, all positive
acceptance predicates, and one negative case per predicate. Hosted acceptance
requires 12/12 accepted from the deployed SHA; test fixtures cannot close it.

## 9. SD-MGMT-01 — Real Management journey

### 9.1 Test placement and helper reuse

Implement in `ajoe734/execute-plans`, based on branch `dev`. Keep PR #601's
route-mocked tests only as component/fixture tests and name/tag them
accordingly. Add a hosted journey spec that has no `page.route` interception
for `/bff/**`, identity endpoints, or domain writes.

Reuse:

- `src/lib/auth/devLoginHelper.ts` and `bffBrowserSession.ts` for product code;
- `e2e/helpers/auth.ts` only through its hosted/external real-session path;
- `scripts/export-dev-login-token.mjs` or the hosted acceptance harness to
  obtain a short-lived server-side test session;
- `scripts/accept-management-hosted-production.mjs` network/error collection.

No token is embedded into the frontend bundle or committed. Missing hosted
credentials, FE/BFF identity, or strict-live configuration fails preflight; it
must not call `test.skip` and report success.

### 9.2 Journey contract

One test run must:

1. open the hosted Management/Management AI entry point;
2. establish a real browser session and verify `/bff/me`;
3. submit a Management AI prompt through the real BFF provider route;
4. verify a non-fixture answer and cited/read-only diagnostic evidence;
5. invoke one allowed product action from the UI;
6. observe the real domain command/status/terminal receipt;
7. reload the page and recover the same conversation/action state;
8. store request IDs, entity IDs, receipt IDs, FE/BFF SHAs, timestamps, and
   screenshots/traces in the acceptance artifact.

Assertions include HTTP status, response provenance, visible UI state, domain
state readback, persistence after reload, and absence of mock/fixture headers.
The action is bounded to dev/test data and must not perform production or
capital-affecting work.

Negative tests cover provider unavailable, BFF error, rejected domain action,
expired session, reload before terminal state, and a deliberate network mock
detector that fails hosted mode.

## 10. SD-AGORA-01 — Real Agora journey

Use the active execute-plans pages and shared drawer:

- `src/agora/pages/strategy-workshop/StrategyWorkshopPage.tsx`;
- `src/agora/pages/trading-room/TradingRoomPage.tsx`;
- `src/agora/components/CandidateReviewDrawer.tsx`.

Add `e2e/agora-product-journey.spec.ts` (or the repository's equivalent hosted
naming convention). It must use the same short-lived hosted session and
strict-live network policy as Management.

The journey is one correlated run:

```text
Workshop prompt
 -> strategy reconstruction
 -> registry draft
 -> Research stage progression
 -> candidate pool
 -> shared CandidateReviewDrawer decision
 -> performance suggestion
 -> policy handoff
 -> Consultation
 -> Governance terminal receipt
```

The test discovers created IDs from responses/UI; it must not use a prebuilt
candidate, fixed journey ID, fixed lens, or route fixture. Reload once after the
candidate decision and once during Consultation, then prove the same correlated
journey resumes.

The evidence artifact records every stage's request ID, entity ID, status,
timestamp, terminal/next receipt, FE/BFF SHA, network trace, and screenshots.
Negative cases cover reconstruction failure, rejected candidate, missing policy
handoff, terminal domain rejection, stale reload state, and mock detection.

## 11. SD-CLEAN-01 — Consolidation without destructive guesswork

Run existing `PFG-BE-CONSOLIDATE-20260820` and
`PFG-FE-CONSOLIDATE-20260820` after the replacement journeys pass. Each
candidate receives one disposition:

```text
keep canonical | migrate callers then delete | delete dead | retain rollback-temporarily
```

Required backend candidates include the legacy Lifecycle JSON reader/writer,
static paper runtime profile, static loop runtime/task fields,
`services/policy-learning/agora_dataset_authority.py`, and obsolete Source
scheduler paths. Required frontend candidates include duplicate `src/lib/bff`
and `src/lib/bff-v1` surfaces and production imports of `@/mocks/seed`.

For every deletion, record:

- symbol/path;
- all static callers found by `rg` and build tooling;
- runtime/network caller evidence where applicable;
- canonical replacement;
- migration commit;
- positive replacement test;
- negative test proving the removed path is unavailable;
- rollback decision and expiry if temporarily retained.

Do not create forwarding wrappers merely to keep dead paths alive. Keep a
compatibility adapter only when a live caller is named and its removal task and
expiry are recorded.

## 12. SD-REL-01 — Exact-pair integration and hosted acceptance

`PFG-HOSTED-ACCEPT-20260820` runs only after component tasks merge. The release
candidate manifest must identify full Pantheon/BFF and execute-plans/FE SHAs,
image digests or bundle hash, migrations, configuration profile, timestamp,
and rollback candidate.

Gate-before-switch sequence:

1. deploy candidate without switching the hosted symlink/route;
2. run DB migration/readiness and canonical telemetry preservation checks;
3. run Lifecycle parity/readback and paper-session checks;
4. run 12/12 loop truth;
5. run Management and Agora hosted journeys against the candidate;
6. switch only if every required gate passes;
7. probe the public hosted origin and verify it serves the exact manifest;
8. retain the prior exact pair for rollback.

A newer Git commit, successful build, healthy supervisor, or fixture test is not
deployment evidence. Failure before switch leaves the hosted pair unchanged.
Failure after switch restores the prior pair and reruns identity/readiness
probes.

## 13. Worker-ready task boundaries

Do not supersede the six existing nonterminal product tasks. Amend their packet
scope with this SD and create only the three new task families (Data, Lifecycle,
and Paper), containing the five sequential work packages that do not already
have canonical owners.

| Task/work package | Files exclusively owned during implementation | Depends on | Completion evidence |
|---|---|---|---|
| SA-DATA-01 | deploy script and its focused tests | none | deploy sentinel tests + dev deploy |
| SA-DATA-02 | baseline evidence tool/schema/docs | none | signed/hash-addressed baseline artifact |
| SA-LIFECYCLE-01 | projector, store, migrations, migration/parity scripts/tests | DATA-01/02 | backfill + two parity windows |
| SA-LIFECYCLE-02 | BFF reader/config/readback tests | LIFE-01 | candidate relational read + rollback |
| SA-PAPER-01 | admission helper, RuntimeBinding metadata transition, reconciler/producer tests | none | pause/resume/restart proof, zero recurring pull |
| `PFG-L12-TRUTH-CROSSLOOP-20260820` | loop registry/inventory/adapters/tests | none | hosted 12/12 exact-SHA truth |
| `PFG-MGMT-JOURNEY-E2E-20260820` | Management hosted E2E/helpers only | backend behavior available | real trace + reload + receipt |
| `PFG-AGORA-JOURNEY-E2E-20260820` | Agora hosted E2E and required UI fixes | backend behavior available | correlated full journey artifact |
| `PFG-BE-CONSOLIDATE-20260820` | inventoried backend candidates | replacement proofs | disposition ledger + tests |
| `PFG-FE-CONSOLIDATE-20260820` | inventoried frontend candidates | replacement proofs | disposition ledger + tests |
| `PFG-HOSTED-ACCEPT-20260820` | release manifest/evidence only | all above | exact-pair hosted acceptance |

Maximum safe first wave is DATA-01, DATA-02, PAPER-01, loop truth, Management
E2E, and Agora E2E in separate worktrees. LIFE-01 starts after DATA tasks;
LIFE-02 follows LIFE-01. Consolidation follows replacement proof. Hosted
acceptance is last. Two workers must never edit the same repository paths or
implement the same task under new IDs.

## 14. Definition of done

The implementation program is complete only when:

- canonical telemetry survives repeated dev deployment and history disposition
  is explicit;
- relational Lifecycle backfill, shadow, restart, reader cutover, rollback, and
  JSON retirement all pass with bounded storage;
- stale manual Source input pauses the existing RuntimeBinding and a newer
  manual snapshot resumes it without recurring provider egress;
- all twelve product loops are accepted from fresh runtime evidence at the
  deployed SHA;
- Management and Agora complete their real hosted journeys with reload and
  terminal receipt proof;
- duplicate/dead paths have caller-backed dispositions and only proven cleanup
  is merged; and
- the hosted FE/BFF exact pair and manifest pass the complete acceptance suite.

Anything less remains an open task or blocker; it must not be represented as
product completion.
