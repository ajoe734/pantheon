# Lifecycle Projector Incremental Redesign Plan — 2026-08-01

Status: archived design authority; implementation not yet claimed

Execution catalog:
`docs/bff/execution-tasks/2026-08-01-lifecycle-projector-incremental-redesign/`

Incident evidence:
`docs/deployment/evidence/lifecycle-projector/LIFECYCLE-PROJ-INCIDENT-20260801/`

## 1. Decision

Replace the file-backed, full-history lifecycle projection with a Postgres-backed
incremental projection. `telemetry_events` remains the immutable canonical event
authority. The projector keeps only one bounded fetch batch and the affected
journey aggregates in process memory; it does not retain canonical history,
identity chains, or complete read models in a Python object graph.

The existing JSON implementation remains stopped in dev until the emergency
containment is reviewed and deployed. It may be used only as a bounded shadow
oracle during migration. It must not be restarted against the current 4.3 GiB
state without an explicit incident decision.

This plan does not authorize live-capital operation, production cutover, data
deletion, or bypass of independent review.

## 2. Objective and success boundary

The target projector must:

- consume committed lifecycle rows from `telemetry_events` in monotonic
  `ingested_seq` order;
- preserve existing canonical identity, duplicate, quarantine, live/recovery,
  freshness, and tenant/environment isolation semantics;
- update only the journeys, stages, identity links, loop runs, and controller
  rows affected by the current batch;
- serve Trade Journey and loop-run reads from indexed Postgres queries with
  bounded keyset pagination;
- recover from process death with RPO=0 for admitted commits and without a
  full-history in-memory rebuild;
- support a shadow/backfill path, deterministic parity checks, canary cutover,
  and an immediate configuration rollback to the last accepted reader;
- meet the resource and latency gates in section 14 before dev cutover.

Success is not a passing unit test or a newly started container. It requires
merged, independently reviewed changes; exact deployment identity; authoritative
pre/post readback; failure and restart evidence; capacity evidence; and a
rollback-safe dev cutover.

## 3. Incident evidence and root cause

### 3.1 Runtime evidence captured on 2026-08-01

| Observation | Evidence |
| --- | ---: |
| VM memory | 47 GiB RAM, no swap |
| Projector before stop | approximately 11.2 GiB RSS and 98.7% CPU during startup/rebuild |
| Container limit before stop | none (`HostConfig.Memory=0`) |
| Container restart policy before stop | `unless-stopped` |
| `controller_state.json` | 4,514,024,955 bytes |
| Abandoned state temp file | 1,870,938,338 bytes |
| Projection directory | 59 GiB |
| `generations/` | 53 GiB across 32 directories |
| Canonical events in state | approximately 686,028 |
| Loop runs in projection | approximately 97,181 |
| Memory after stop | 5.7 GiB used, 41 GiB available |
| Projector final state | exited at `2026-08-01T09:17:32Z`, exit 137, `OOMKilled=false` |
| BFF after stop | `/health` 200; `/healthz` degraded and `ready=false` because projector freshness is stale |

The projector was deliberately stopped as a temporary live repair. No state,
generation, or canonical event was deleted. The last atomic read-model bundle is
still available, but it is correctly reported as stale.

### 3.2 Source evidence

The current implementation has four multiplicative costs:

1. `LifecycleProjector.project_records()` deep-copies `self.state` before every
   batch.
2. Every accepted canonical event and its original payload is retained forever
   in `state["canonical_events"]`.
3. Every journey identity chain is retained forever in
   `state["identity_chains"]`.
4. `_render()` sorts all canonical entries, regenerates all journey events,
   runs a full `JourneyMaterializer.rebuild()`, regenerates all loop records,
   and then `_publish_candidate()` serializes the full state and both complete
   read models.

Consequently, a single batch can simultaneously retain the old state, a deep
copy, canonical-entry and journey-event lists, a materializer aggregate graph,
loop records, JSON encoder buffers, and prepared temporary files. Batch size 500
limits only new input rows; it does not bound any of those historical structures.

This is a data-model and update-algorithm defect, not a conventional unreachable
object leak. Reducing batch size cannot make the current design bounded.

## 4. Current and target data flow

Current:

```text
telemetry_events -> fetch 500 -> deepcopy 4.3 GiB state
  -> append to all-history dictionaries
  -> rebuild every journey and loop run
  -> serialize state + two complete JSON read models
  -> copy generation + atomically switch current symlink
  -> BFF loads JSON and materializes/caches it
```

Target:

```text
telemetry_events -> fetch bounded batch -> one database transaction
  -> receipt/dedup check
  -> identity lookup/upsert
  -> stage insert
  -> affected journey + loop aggregate upsert
  -> contiguous checkpoint/revision update
  -> commit

BFF -> tenant-scoped indexed SQL -> bounded DTO page/detail/timeline/graph
                                      + controller freshness metadata
```

Postgres is both the durable incremental projection store and the atomic publish
boundary. There is no generation directory and no whole-store file switch in
the target architecture.

## 5. Required invariants

### 5.1 Authority and atomicity

- `telemetry_events` is the only canonical lifecycle event authority.
- A projection row is never presented as source truth; every row retains its
  source `event_id` and `ingested_seq` lineage.
- Event receipt, identity changes, stage row, journey aggregate, loop aggregate,
  and checkpoint advance commit in one transaction.
- Checkpoint advances only to the highest source row whose disposition is
  durably recorded as `applied`, `duplicate`, `ignored`, or `quarantined`.
- A database rollback leaves no partial aggregate and no checkpoint advance.
- Only live-mode processing advances `last_live_success_at` and
  `accepted_live`. Backfill/recovery/replay never manufacture live freshness.

### 5.2 Identity and idempotency

- `event_id` is immutable. Reuse with the same canonical fingerprint is an
  idempotent duplicate. Reuse with a different fingerprint is a fail-closed
  conflict that blocks checkpoint advance at that row.
- A journey identity is scoped by `(tenant_id, environment, journey_id)`.
- Every stable identity value resolves to at most one journey inside its
  tenant/environment scope.
- A new event that changes a previously admitted stable identity is quarantined;
  it does not silently fork or rewrite the chain.
- Stage identity is `(tenant_id, environment, journey_id, source_event_id,
  stage_name)`. Duplicate delivery cannot create duplicate stages.
- Ordering uses canonical stage sequence, event sequence, source time, and
  `ingested_seq` as deterministic tie breakers. Arrival order is not business
  order.

### 5.3 Boundedness

- Process memory contains at most the configured input batch, per-batch maps,
  and affected aggregate rows.
- No endpoint or worker path may load all journeys, stages, loop runs, receipts,
  or source events into memory.
- List/timeline APIs use keyset pagination, have a default page size of 50, and
  reject a page size greater than 200.
- Full raw payloads remain in `telemetry_events`; the projection stores only
  contract fields, lineage, status summaries, and explicitly allowed evidence
  references.
- Receipts may be partitioned/retained according to section 13, but dedupe
  correctness must not depend on an unbounded Python cache.

### 5.4 Read truth

- Every response retains `projection_schema_version`, `projection_revision`,
  source checkpoint/high watermark, backlog, mode, accepted-live state,
  deployment SHA, and freshness.
- Missing, stale, conflicting, or failed projection state remains degraded or
  unavailable; JSON fallback must not be mislabeled as current Postgres truth.
- Tenant and environment predicates are mandatory at the repository boundary,
  not optional filters added by route handlers.
- An out-of-scope identifier and a nonexistent identifier remain the same 404.

## 6. Target relational model

Use a dedicated `trade_journey_projection` schema. Migration names and exact
column types may follow repository conventions, but the semantic keys and
constraints below are mandatory.

### 6.1 `controller`

One row per `(controller_id, tenant_scope, environment_scope)` or one global row
when the worker has a single declared scope.

Required fields include checkpoint, source high watermark, backlog,
projection revision, deployment SHA, mode, status, accepted-live flag,
last-poll/success/live/recovery/backfill/replay/failure timestamps, last error,
quarantine count, and update time.

The worker takes a Postgres advisory lock for its controller identity and locks
the controller row `FOR UPDATE` in every projection transaction. A second active
writer must fail readiness rather than race.

### 6.2 `event_receipts`

Required fields:

- `event_id` primary key;
- `ingested_seq` unique and indexed;
- canonical SHA-256 fingerprint;
- tenant, environment, journey ID, loop-run ID when resolved;
- source event type and created time;
- disposition (`applied`, `duplicate`, `ignored`, `quarantined`);
- projection revision and projected time.

Do not copy the source JSON payload. An exact duplicate uses the receipt
fingerprint. A conflicting duplicate stops the worker and records an operational
error outside the transaction that failed.

### 6.3 `identity_links`

Primary key:
`(tenant_id, environment, identifier_type, identifier_value)`.

Each row references `(tenant_id, environment, journey_id)`, stores first/last
source sequence and timestamps, and has a check constraint limiting
`identifier_type` to the canonical stable identity registry. A conflicting
unique insert is an identity-chain conflict, not an upsert to a different
journey.

### 6.4 `journeys`

Primary key: `(tenant_id, environment, journey_id)`.

Store the current materialized summary only: state/status, stage coverage,
terminal state, first/last source times and sequences, current identity summary,
bounded evidence/diagnostic summaries, loop-run ID, revision, and update time.
Use JSONB only for bounded schema-defined summaries. Add keyset indexes for the
supported list sorts, beginning with:

- `(tenant_id, environment, updated_at DESC, journey_id DESC)`;
- `(tenant_id, environment, created_at DESC, journey_id DESC)`;
- `(tenant_id, environment, status, updated_at DESC, journey_id DESC)`;
- `(tenant_id, environment, loop_run_id)`.

### 6.5 `journey_stages`

Primary key:
`(tenant_id, environment, journey_id, source_event_id, stage_name)`.

Store stage status, canonical stage ordinal, source sequence, event sequence,
occurred/recorded times, selected contract fields, lineage/evidence references,
projection revision, and fingerprint. Required timeline index:

`(tenant_id, environment, journey_id, stage_ordinal, event_sequence,
occurred_at, ingested_seq, source_event_id)`.

Graph and evidence responses must be assembled from bounded queries for one
journey, not from a global scan.

### 6.6 `loop_runs`

Primary key: `(tenant_id, environment, loop_run_id)`. A unique constraint on
the scoped journey association prevents two loop rows for one canonical journey
unless the domain contract explicitly supports it.

Store the existing loop response contract, lifecycle summary, freshness lineage,
revision, and update time. List index:

`(tenant_id, environment, updated_at DESC, loop_run_id DESC)`.

### 6.7 `quarantine`

Primary key: `(event_id, ingested_seq)`. Store reason code, a bounded redacted
reason detail, source type, scoped identity fields that passed validation,
fingerprint, first/last seen times, occurrence count, resolution status, and
resolution audit reference. Do not copy unrestricted raw event payloads.

Quarantine is operational truth. Its count and oldest unresolved age feed
readiness and alerts.

## 7. Projection transaction

For each wake-up or poll:

1. Read the lifecycle-event high watermark using the indexed source query.
2. Fetch at most `batch_size` rows above the committed checkpoint, ordered by
   `ingested_seq`.
3. Begin a database transaction and lock the controller row.
4. Reconfirm the checkpoint. Discard already completed rows from a concurrent or
   prior worker attempt.
5. For each remaining row:
   - validate source row/payload agreement and canonical event fields;
   - compute the canonical fingerprint;
   - resolve receipt and duplicate/conflict disposition;
   - validate the correlation envelope and stable identity;
   - acquire identity links without allowing reassignment;
   - derive zero or more bounded stage mutations;
   - insert stages idempotently;
   - reduce only the affected journey and loop aggregate;
   - insert the event receipt/disposition.
6. Advance the checkpoint to the highest contiguous row with a durable
   disposition and increment `projection_revision` once for the transaction.
7. Update controller mode, backlog, timestamps, quarantine count, and live-truth
   fields under the rules in section 5.
8. Commit. Emit metrics or a notification only after commit.

Invalid lifecycle events are quarantined and may advance the checkpoint because
their disposition is durable. An `event_id` fingerprint conflict, database
constraint inconsistency, unknown schema version, or reducer invariant failure
rolls back and stops at that source position for governed resolution.

The worker retries serialization/deadlock/transient connection failures with
bounded exponential backoff and jitter. It never retries a deterministic
contract conflict forever.

## 8. Reducer contract

Extract the pure canonicalization and stage-reduction logic from the existing
file publisher. A reducer input is one canonical source row plus the current
scoped aggregate; output is a typed mutation set, not a complete global model.

The pure reducer must prove:

- identical source input and aggregate state produce byte-equivalent normalized
  mutation payloads;
- duplicate and out-of-order application converge;
- terminal-state conflicts remain diagnostics/degraded truth;
- all existing lifecycle event types and dev-only fixture restrictions are
  preserved;
- sensitive live fields follow the existing BFF visibility rules;
- backfill/replay labeling cannot make a row accepted live;
- the reducer has no file, database, wall-clock, or network dependency except an
  injected timestamp where the contract requires one.

## 9. BFF read design and compatibility

Add a Postgres projection repository behind the current Trade Journey and
loop-run DTOs. Keep route paths and existing response fields compatible:

- `/bff/management/trade-journeys`
- detail, timeline, graph, resolve, evidence, replay, metrics, attention, and SSE
  routes under the same family;
- `/bff/v5/loop-runs` list/detail and downstream loop health/composition views.

The repository must expose explicitly tenant/environment-scoped methods for
page, detail, timeline, identity resolution, graph/evidence, loop page/detail,
and controller freshness. Route code must not be able to call an unscoped list.

Page tokens are opaque, signed or integrity-protected encodings of the last sort
tuple plus filter/sort version. A token used with different filters, tenant,
environment, or sort returns a validation error. Offset pagination is not
accepted for unbounded tables.

During shadow mode, the BFF continues serving the last accepted backend while a
read-only parity probe compares both stores. Cutover uses a single backend flag;
no route may independently drift between JSON and Postgres. A rollback changes
that flag back and restarts only the BFF, not the legacy projector.

## 10. Freshness, SLO, and observability

Required metrics, partitioned by mode/environment but never by unbounded IDs:

- source high watermark, committed checkpoint, and backlog;
- batch rows and transaction duration;
- applied/duplicate/ignored/quarantined/conflict counters;
- projection revision and last successful/live commit ages;
- process RSS, Python heap where available, CPU, DB pool usage, and retry count;
- BFF page/detail query latency and returned row count;
- unresolved quarantine count/age and identity-conflict count;
- shadow parity mismatch count by bounded reason code.

Required alerts:

- stale last poll or live commit;
- backlog above configured count or age;
- worker absent, second writer, repeated restart, or memory-limit termination;
- source conflict/reducer invariant failure;
- database storage/index growth anomaly;
- parity mismatch during migration;
- BFF projection-query errors or p95 regression.

Readiness remains fail closed. Liveness only proves the process can answer; it
must not hide stale projection truth.

## 11. Migration and parity

### Phase A — schema and shadow writer

- Apply additive schema migrations; do not alter or truncate
  `telemetry_events`.
- Backfill from source sequence 0 into the new tables with mode `backfill` and a
  separate migration watermark.
- Start the incremental worker from the high watermark captured at backfill
  start, close the delta, then enter shadow-live mode.
- Keep the legacy projector stopped. If parity requires a legacy reference,
  compare against the last accepted JSON generation or run a bounded offline
  converter in an isolated resource-limited job.

### Phase B — deterministic parity gate

Compare scoped counts and stable hashes for:

- controller checkpoint/high watermark/backlog semantics;
- journey identities and all stable identity links;
- stage keys, order, status, timestamps, and evidence references;
- journey summary/status/completeness/diagnostics;
- loop-run identity, status, lifecycle, and freshness fields;
- duplicate, out-of-order, quarantine, replay, and recovery fixtures.

Every mismatch is classified as an intended documented contract change or a
blocking defect. Unexplained mismatch count must be zero.

### Phase C — dev canary and cutover

- Deploy an exact merged SHA with the worker in shadow mode and BFF still on the
  accepted reader.
- Meet capacity, parity, security, and recovery gates.
- Enable Postgres reads for an authorized dev canary scope, then all target-dev
  paper reads.
- Observe at least 24 hours and one real paper lifecycle with backlog zero and
  no unexplained parity mismatch.
- Record deployment manifest, image digest, configuration, controller row,
  authenticated BFF readback, and rollback probe.

### Phase D — retirement

After at least seven days of accepted dev soak and explicit operator approval:

- disable/remove the legacy JSON writer and file readers;
- archive checksums and selected redacted controller/parity evidence;
- dry-run and then remove obsolete generation directories from dev only;
- retain canonical source events and relational projection tables;
- update runbooks and remove obsolete configuration.

No task in this packet authorizes production migration or production deletion.

## 12. Rollback and recovery

Before Postgres read cutover, rollback means leave the current accepted BFF
reader unchanged and stop the new shadow worker. The source event log is
untouched.

After dev cutover but before legacy retirement, rollback means set the BFF
reader flag to the last accepted backend, redeploy/restart only the BFF, and
leave the new worker stopped for diagnosis. If the legacy JSON bundle is stale,
the system must say stale; rollback does not manufacture freshness.

After legacy retirement, application rollback is by schema-compatible prior
binary. Database migrations are expand/contract and must remain backward
compatible through the soak window. A destructive down migration is prohibited.
If recomputation is required, truncate only the new projection schema after an
explicit operator decision, then rebuild from `telemetry_events`; never truncate
the canonical source table.

## 13. Retention and storage

- Canonical raw history stays in `telemetry_events` under its separately
  governed retention policy.
- Journey/stage/loop aggregates persist while product read contracts require
  them. Archive/partition policy is a separate, evidence-driven decision.
- Event receipts may be range-partitioned by `ingested_seq` or projected month.
  A receipt partition cannot be dropped unless canonical `event_id` uniqueness
  and replay idempotency remain provable; a compact immutable dedupe key may be
  retained longer than detailed receipt metadata.
- Resolved quarantine records may be archived after their audit references are
  durable; unresolved records remain queryable.
- Legacy generation retention is temporarily reduced from 32 to 4 by hotfix.
  Existing files are not automatically deleted. Retirement uses a dry-run,
  explicit path allow-list, checksum manifest, and operator approval.

## 14. Quantitative acceptance gates

All gates run on a resource profile no larger than the current 12-vCPU/47-GiB
dev VM, with the worker limited to 4 GiB for the target design.

| Gate | Required result |
| --- | --- |
| Scale corpus | at least 1,000,000 canonical lifecycle events and 150,000 loop runs |
| Steady worker RSS | at most 2.0 GiB |
| Peak worker RSS | at most 2.5 GiB during catch-up, replay, or a 500-row batch |
| Memory slope | adding events 500k -> 1M grows steady RSS by at most 256 MiB |
| Batch latency | 500-row transaction p95 at most 5 seconds on dev profile |
| Normal freshness | backlog age p95 at most 30 seconds in steady dev load |
| Restart recovery | SIGKILL at arbitrary transaction points, RPO=0, no duplicates, backlog returns to zero |
| Catch-up | current baseline plus 100k-row backlog completes within 30 minutes |
| Read latency | list/detail/timeline p95 at most 300 ms/300 ms/500 ms for page <= 200 |
| Query plan | common reads use declared indexes; no unbounded sequential scan |
| Parity | zero unexplained mismatch across full backfill and required fault fixtures |
| Isolation | cross-tenant/environment probes return identical protected 404 semantics |

If a gate cannot be met, the worker task records the blocker; it must not relax
the threshold silently.

## 15. Security and governance

- Reuse existing BFF authentication, RBAC, tenant, MFA/two-person, environment,
  and live-sensitive-field controls.
- Projection database credentials are least privilege: source SELECT, projection
  schema DML, and no source DELETE/UPDATE.
- Migration credentials are distinct from runtime credentials.
- No unrestricted raw payload is copied to projection/quarantine/logs/evidence.
- Evidence is redacted, append-only, checksummed, and contains exact commit,
  deployment, command, result, and reviewer identities.
- `Codex` and `Codex2` are one identity and cannot independently review this
  work. Every implementation PR needs an eligible independent reviewer.
- Supervisor is the only routine dispatcher. Workers use clean task worktrees,
  declared file scopes, PRs to `dev`, required checks, and reviewed merges.

## 16. Declared scope and non-goals

In scope:

- lifecycle projection persistence and worker;
- pure incremental journey/loop reduction;
- BFF projection reads and freshness;
- shadow migration/parity tooling;
- dev capacity, deployment, cutover, rollback, and legacy retirement evidence.

Out of scope:

- changing canonical telemetry ingestion or event meaning;
- changing frontend information architecture;
- supervisor, auto-worker, task-state, or deployment-authority redesign;
- live trading, broker orders, capital effects, or production cutover;
- automatic deletion of canonical data or current legacy state;
- broad refactors of `read_store.py` unrelated to loop-run compatibility.

## 17. Work packages, dependency order, and merge order

| Order | Task | Purpose | Depends on |
| ---: | --- | --- | --- |
| 0 | `LIFECYCLE-PROJ-HOTFIX-REVIEW-20260801` | independently review/merge emergency containment | none |
| 1 | `LIFECYCLE-PROJ-STORE-001` | additive schema and transactional repository | none |
| 2 | `LIFECYCLE-PROJ-REDUCER-001` | pure reducer and bounded incremental worker | `STORE-001` |
| 3 | `LIFECYCLE-PROJ-BFF-001` | indexed, paginated BFF reader | `STORE-001` |
| 4 | `LIFECYCLE-PROJ-MIGRATE-001` | backfill, shadow, parity, and conflict tooling | `REDUCER-001`, `BFF-001` |
| 5 | `LIFECYCLE-PROJ-CAPACITY-001` | million-event resource/fault/read gates | `REDUCER-001`, `BFF-001` |
| 6 | `LIFECYCLE-PROJ-CUTOVER-001` | exact-SHA dev canary, cutover, and rollback proof | hotfix, `MIGRATE-001`, `CAPACITY-001` |
| 7 | `LIFECYCLE-PROJ-RETIRE-001` | seven-day closeout and guarded legacy retirement | `CUTOVER-001` |

Tasks 2 and 3 may run in parallel after task 1. Tasks 4 and 5 may run in
parallel after tasks 2 and 3. All later merges follow the dependency graph.
Sequential dependencies deliberately permit later tasks to touch prior scopes
for integration without parallel file ownership.

Every packet declares its own expected branch, clean worktree, exact artifacts,
acceptance, validation, rollout, rollback, and reviewer. The machine catalog is
the dispatch source; prose summaries do not override it.

## 18. Validation matrix

Each implementation task runs focused unit/integration tests plus adjacent
contract tests. The program cutover additionally requires:

- migration up/idempotent/restart tests against real Postgres;
- duplicate, conflicting duplicate, invalid identity, out-of-order, replay,
  backfill, recovery, and transaction-failure tests;
- property/differential reducer tests against accepted legacy fixtures;
- authenticated Trade Journey and loop-run route tests, pagination stability,
  token tamper, cross-scope negatives, and sensitive-field redaction;
- one-million-event capacity and query-plan evidence;
- SIGTERM/SIGKILL, DB disconnect, deadlock/retry, second-writer, and disk/DB
  pressure fault injection;
- hosted exact-SHA paper lifecycle, controller freshness, BFF readback, and
  rollback rehearsal.

Required evidence location for each task is:

`docs/deployment/evidence/lifecycle-projector/<TASK-ID>/`

The final evidence manifest must include checksums and direct references to the
implementation PR, merge SHA, independent review, required checks, deployment
identity, test commands/results, authoritative readbacks, residual risks, and
rollback result.

## 19. Immediate incident posture

Until the execution graph reaches an accepted cutover:

- keep `pantheon-loop-run-projector-scheduler-1` stopped on the current dev
  implementation;
- keep serving the last atomic bundle while `/healthz` remains degraded/stale;
- merge and deploy the emergency defaults only after independent review;
- do not delete the 59 GiB projection directory as an incident shortcut;
- do not raise the VM size or lower batch size as a claimed root-cause fix;
- escalate if BFF availability, canonical telemetry ingestion, disk headroom,
  or other services degrade.
