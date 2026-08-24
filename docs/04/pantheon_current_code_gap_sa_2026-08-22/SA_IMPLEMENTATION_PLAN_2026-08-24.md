# Pantheon Functional Closure SA Implementation Plan — 2026-08-24

Status: implementation architecture derived from
[`CURRENT_GAP_2026-08-24.md`](CURRENT_GAP_2026-08-24.md)

Priority: functional completeness and operability before additional governance

Delivery scope: Pantheon backend, `execute-plans` frontend, dev VM deployment, and
hosted functional proof

## 1. Objectives and constraints

The program completes the remaining product paths without introducing a parallel
architecture. It must:

- make PostgreSQL the normal Lifecycle projection and BFF read authority;
- retire the active unbounded JSON Lifecycle store immediately after a successful
  cutover/restart/readback;
- preserve exact FE/BFF evidence while removing repeated manual pair authorization;
- finish real Agora, Management, and Management AI paper-only journeys;
- simplify backend/frontend code only where caller and replacement evidence supports
  deletion; and
- finish with the public dev frontend read-only and Source Ingestion reconcile-only.

It must not create another Lifecycle service, database schema, release controller,
task identity, frontend repository, or compatibility API. It must not execute live
capital actions or recurring external Source pulls.

## 2. Architecture decisions

### AD24-01 — Functional gates are on the critical path; governance ceremonies are not

Required gates are those that prevent incorrect product behavior: source catch-up,
read/write authority consistency, restart readback, real journey persistence, and
exact deployed identity. A seven-day soak, a retirement HMAC CLI, repeated human
approval of a dev-paper pair, and review-attestation mechanics are not functional
dependencies and are removed from this plan's critical path.

### AD24-02 — Canonical telemetry remains the source; PostgreSQL projection becomes the read model

`public.telemetry_events.ingested_seq` remains the ordered canonical input.
`trade_journey_projection` is the only target relational projection schema. The
existing `ProjectionStore`, migration, controller, and BFF PostgreSQL reader are
extended and activated; they are not duplicated.

### AD24-03 — Preserve the service identity and replace its implementation

The Compose key `loop-run-projector-scheduler` is retained because deploy scripts,
probes, and dependencies already reference it. Its target behavior is a bounded,
transactional PostgreSQL projector. It must no longer serialize full-state JSON
generations during normal operation.

### AD24-04 — PostgreSQL cutover is one functional state transition

Backfill, catch-up, BFF reader switch, readiness switch, restart/readback, and exact
legacy deletion are one controlled transition. JSON remains readable only until the
relational state is proven. There is no dual-authority steady state and no time-based
soak requirement.

### AD24-05 — Exact pair binding is automatic and immutable

Every proof is bound to the immutable release candidate's FE SHA, BFF SHA, pair ID,
profile, and manifest. The release controller derives these values and verifies them
against the served deployment. Operators do not re-authorize normal paper-only proof
when a candidate SHA changes.

### AD24-06 — Journey acceptance remains end-to-end

Removing manual approval does not turn the proof into a read-only smoke test.
Management/AI and Agora must create real paper-domain state through hosted APIs,
observe terminal receipts, reload, and read back persisted results. Fixture, seed,
prebuilt-ID, mock-provider, and memory-only shortcuts remain invalid.

### AD24-07 — Cleanup follows replacement proof

Read-only caller audits can begin immediately. A code path is deleted only after all
callers are accounted for and its replacement journey passes. Lifecycle JSON is the
special case where the replacement proof is the PostgreSQL restart/readback gate;
after that gate, its exact legacy directory is deleted immediately.

### AD24-08 — Existing task identities and history are preserved

The seven current nonterminal task IDs remain canonical. Corrected contracts are
attached to them; they are not marked superseded and not re-created under new IDs.
Pantheon PR #5147 is replaced as a delivery vehicle, not as a task identity.

### AD24-09 — Dev external effects stay bounded

Source Ingestion is reconcile-only. A test may explicitly request one manual pull.
The functional window is paper-only, has a bounded duration, and always restores the
public profile to read-only. The fixed dev VM SSH path is the delivery dependency;
GCP billing APIs are not.

## 3. Target architecture

### 3.1 Lifecycle data flow

```text
public.telemetry_events (canonical, ordered by ingested_seq)
                         |
                         v
loop-run-projector-scheduler (same Compose service key)
  - one advisory-locked controller scope
  - bounded batch transaction
  - idempotent event receipts
  - journeys/stages/identity links/loop runs/quarantine
                         |
                         v
trade_journey_projection (PostgreSQL read authority)
             |                           |
             v                           v
 BFF journey/loop endpoints       BFF /readyz Lifecycle dependency
```

The JSON generation bundle is a migration input only until cutover. It is not a
fallback after PostgreSQL is selected and is deleted after the restart/readback gate.

### 3.2 Lifecycle controller state

```text
disabled
   -> backfill (accepted_live=false)
   -> recovery/catch-up (accepted_live=false)
   -> live-ready (checkpoint=source high, backlog=0, quarantine=0)
   -> live-accepted (exact deployment SHA)
   -> BFF cutover
   -> restart/readback
   -> legacy JSON deleted
```

Any failure before BFF cutover leaves JSON as the accepted reader. A failure after
the switch but before legacy deletion rolls the reader and worker configuration back
to the last known-good JSON candidate. Once restart/readback passes and JSON is
deleted, rollback is forward-only: rebuild the relational projection from canonical
telemetry rather than restoring an obsolete JSON writer.

### 3.3 Functional proof and release flow

```text
current Pantheon dev + current execute-plans dev
                |
                v
immutable release candidate
  {fe_sha, bff_sha, pair_id, profile=write-proof, expiry}
                |
                v
gate-before-switch -> deploy -> manifest/version verification
                |
                +-------------------------+
                |                         |
                v                         v
       Management/AI journey         Agora journey
                |                         |
                +-----------+-------------+
                            v
                 consolidation verification
                            v
                  final hosted acceptance
                            v
          public read-only + Source reconcile-only
```

The workflow obtains the test account from the installed secret store. It never
materializes credentials into task JSON, evidence, logs, or artifacts.

### 3.4 Authority boundaries

| Concern | Owner | Must not own |
|---|---|---|
| Canonical events | `public.telemetry_events` | derived Lifecycle state |
| Lifecycle projection | `trade_journey_projection` plus its worker | source ingestion or task state |
| Product reads/actions | operator BFF | repository/task mutation |
| Product UI | `execute-plans` | supervisor/V2 TaskStore |
| Candidate/version delivery | cross-repo release/deploy workflow | product truth or manual per-SHA approval |
| Development task dispatch | supervisor/V2 TaskStore | product readiness |

## 4. Work packages mapped to canonical tasks

### WP-01 — Minimal relational Lifecycle completion

Canonical task: `LIFECYCLE-PROJ-RETIRE-001`

Delivery rule: clean branch from current `dev`; do not merge Pantheon PR #5147 as-is.

Retain from current code/PR direction:

- existing relational schema and `ProjectionStore`;
- transactional event receipts and controller semantics;
- existing BFF PostgreSQL read surface and readiness checks; and
- the existing Compose service key and deployment integration.

Remove from the delivery:

- HMAC retirement-command framework;
- seven-day soak/approval state machine;
- claims based on hand-authored or stale hosted evidence; and
- normal-operation full-state JSON writes/generations.

Acceptance:

- real telemetry backfill populates all applicable relational tables;
- controller is `ready`, `live`, `accepted_live=true`, exact-SHA, caught up, with zero
  backlog and zero unresolved quarantine;
- BFF reads/readiness are PostgreSQL-only;
- projector and BFF restart, and the same sampled IDs are readable;
- exact `/data/bff/lifecycle-projection` legacy content is deleted;
- approximately 21 GiB is released and the directory does not regrow.

### WP-02 — Candidate-bound bounded functional proof

Canonical task: `PFG-BOUNDED-FUNCTIONAL-CLOSURE-PROOF-20260824`.

Change the release workflow so the parent candidate is the sole source of FE/BFF
identity. It passes generated immutable values to child workflows, verifies the
served manifest/version, opens a time-bounded paper write profile, runs journeys, and
restores read-only in both success and failure paths.

Acceptance:

- no task packet or operator prompt contains a stale hard-coded pair prerequisite;
- a mismatch between candidate and served identity still fails closed;
- a timeout/failure still invokes read-only restoration; and
- Source remains reconcile-only and no live-capital action is available.

### WP-03 — Management/Management AI functional closure

Canonical task: `PFG-MGMT-JOURNEY-E2E-20260820`.

Repair the actual OpenClaw provider path, then run the complete hosted journey. Reuse
merged `execute-plans` PR #601 behavior and only compatible parts of open
`execute-plans` PR #613. Do not accept an `OPENCLAW_RESPONSES_UNREACHABLE` error as
the provider-answer step.

Acceptance:

- real Formula, Activity, Paper Telemetry, and Postmortem reads or truthful typed
  unavailable states;
- an actual Management AI provider answer with network provenance;
- one confirmed supported paper-domain action exactly once;
- terminal receipt/readback and reload persistence; and
- zero fixtures, seed imports, or prebuilt domain IDs.

### WP-04 — Agora functional closure

Canonical task: `PFG-AGORA-JOURNEY-E2E-20260820`.

Use the merged `execute-plans` PR #612 product implementation. Add only the fixes
exposed by the real hosted journey.

Acceptance:

- Workshop and Consultation create the journey inputs;
- Trading Room uses a real workspace/pool;
- decision and performance state are backed by the BFF;
- reload/fresh read returns the same objects; and
- the evidence is correlated to the exact candidate.

### WP-05 — Caller-backed consolidation

Canonical tasks: `PFG-BE-CONSOLIDATE-20260820` and
`PFG-FE-CONSOLIDATE-20260820`.

The inventory phase runs read-only in parallel. It enumerates imports, routes,
workflows, deployment references, tests, docs, and runtime callers. Actual changes
follow `retain`, `replace_then_delete`, `delete`, or `defer` dispositions.

Acceptance:

- no two active implementations own the same behavior without an explicit adapter
  boundary;
- no compatibility endpoint or frontend copy is added merely to keep old callers;
- affected unit/contract/journey tests pass after deletion; and
- the disposition ledger names replacement proof for every deletion.

### WP-06 — Final exact-pair hosted acceptance

Canonical task: `PFG-HOSTED-ACCEPT-20260820`.

This is the integration closure task, not an implementation bucket. It records the
served exact pair after WP-01 through WP-05 pass.

Acceptance:

- exact manifest and BFF version alignment;
- relational Lifecycle readiness and restart readback;
- complete Management/AI and Agora journey artifacts;
- consolidation validation;
- public read-only restoration; and
- Source reconcile-only confirmation.

## 5. Dependency graph and maximum parallelism

### Wave A — four independent lanes

1. Lifecycle worker/reader current-dev implementation (`WP-01`).
2. Candidate auto-binding and proof-window workflow (`WP-02`).
3. Management AI/OpenClaw provider connectivity repair (`WP-03`, provider portion).
4. Backend and frontend caller inventories (`WP-05`, audit-only sublanes).

These lanes own different files and may proceed simultaneously. The BE and FE audits
may themselves run in separate repositories/worktrees, but destructive cleanup is
not part of Wave A.

### Wave B — deploy and exercise

- deploy WP-01, run real backfill/catch-up, switch reader/readiness, and perform
  restart/readback;
- run Management/AI and Agora full journeys in parallel on the same immutable bounded
  candidate after WP-02 is ready.

Only one candidate may own the dev write-proof lease. Parallel browser journeys may
share that candidate when their test data namespaces are unique.

### Wave C — immediate retirement and proven cleanup

- delete exact Lifecycle legacy JSON after WP-01's restart/readback succeeds;
- execute backend and frontend `replace_then_delete`/`delete` dispositions in
  parallel after their respective replacement journey passes.

### Wave D — integration closure

- deploy the consolidated exact pair;
- rerun bounded acceptance where code identity changed;
- restore read-only and complete `PFG-HOSTED-ACCEPT-20260820`.

```text
WP-01 Lifecycle ---------------------> restart/readback -> JSON delete ---+
WP-02 candidate binding ----+                                           |
WP-03 provider repair ------+--> Management/AI journey -----------------+
                            +--> Agora journey -------------------------+
WP-05 caller audits ----------------> proven BE/FE cleanup -------------+
                                                                         v
                                                          WP-06 hosted acceptance
```

## 6. File ownership and conflict boundaries

| Lane | Primary Pantheon paths | `execute-plans` paths | Exclusions |
|---|---|---|---|
| Lifecycle | `services/trade_journey/`, BFF Lifecycle reader/readiness, related Compose/deploy tests | none | no workflow authorization code |
| Proof binding | `scripts/cross_repo_release_controller.py`, Pantheon integration/deploy workflow contracts | dev deploy/integration workflows and helpers | no product-domain implementation |
| Management AI | OpenClaw adapter/provider and Management BFF integration | Management journey/spec helpers | no Lifecycle paths |
| Agora | Agora BFF fixes exposed by journey | Agora journey/spec helpers | no parallel Agora implementation |
| BE consolidation | inventoried backend candidates only | none | no unproven deletion |
| FE consolidation | none | inventoried frontend candidates only | never copy FE into Pantheon repo |
| Hosted acceptance | manifest/evidence/runbook only | manifest/evidence only | no feature implementation |

One file has one active owner per wave. Cross-lane edits to shared workflows or BFF
startup are integrated by the release lane after feature branches are current with
their respective `dev` branches.

## 7. Validation strategy

| Level | Required proof |
|---|---|
| Unit | projector idempotency, duplicate/conflict handling, controller state, pair derivation, watchdog restoration |
| Contract | Compose env/backend selection, BFF reader/readiness fail-closed behavior, candidate/manifest/version agreement |
| Migration | empty-schema backfill, interrupted resume, catch-up, no duplicate receipt, zero unresolved quarantine |
| Restart | stop/recreate worker and BFF; same journey/loop IDs and controller state remain readable |
| Hosted journey | real browser/network/API/persistence/reload evidence for Management/AI and Agora |
| Cleanup | caller scan, removed-path negative checks, disk-release check, no JSON regrowth |
| Release | exact FE/BFF/pair/profile manifest plus read-only and reconcile-only restoration |

Negative tests are mandatory for stale served identity, missing projection DSN,
checkpoint mismatch, backlog, quarantine, provider unreachable, failed/expired proof
window, duplicate action submission, fixture/seed detection, and remaining callers of
a deletion candidate.

## 8. Migration and rollback

### Before BFF cutover

Failures leave the hosted BFF on JSON. Fix/retry the relational backfill from the
controller checkpoint. No deletion occurs.

### After BFF cutover, before restart/readback

If readiness or readback fails, revert reader/writer configuration to the prior JSON
candidate, preserve the failed relational state for diagnosis, and retry from a clean
candidate. This is the only rollback interval.

### After restart/readback and JSON deletion

The forward path is authoritative. Rebuild from canonical telemetry if repair is
needed. Do not re-enable the full-state JSON writer or restore multi-gigabyte
generations.

### Functional proof

The proof controller invokes public read-only restoration from a finally/watchdog
path. Restoration is idempotent and validated from the served manifest. Test objects
remain paper-domain evidence; no live-capital rollback is relevant because live
capital is never enabled.

## 9. Definition of done

The SA program is complete only when:

- all seven canonical nonterminal tasks are terminal without duplicate task IDs;
- the hosted Lifecycle authority is relational and survives restart/readback;
- the exact legacy Lifecycle directory is removed and remains bounded;
- Management AI returns a real provider answer and Management/Agora write journeys
  pass with persisted reload evidence;
- all cleanup has caller-backed dispositions and no replacement duplication;
- the final hosted manifest identifies the tested exact pair; and
- public dev is read-only, Source is reconcile-only, and no live-capital action was
  exercised.
