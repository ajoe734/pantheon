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

The seven pre-existing nonterminal task IDs retain their immutable specs and history;
their `depends_on`, artifacts, and acceptance are not edited, and they are not marked
superseded. The independent task-graph audit materialized three corrective roots and
two read-only inventory sidecars for work the old contracts cannot safely absorb.
These nodes compose with the seven tasks and are not replacements. Pantheon PR #5147
remains non-mergeable as a bundle.

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

### WP-01 — PostgreSQL Lifecycle activation, then retirement

Corrective root: `PFG-LIFECYCLE-POSTGRES-ACTIVATION-20260824`.

This task owns only the PostgreSQL projector/store and BFF relational reader/readiness
core on a clean current-`dev` branch. It reuses the existing schema,
`ProjectionStore`, controller, receipts, and reader surface. Its acceptance is:

- bounded transactional backfill reaches backlog zero with the exact controller SHA;
- BFF accepted mode reads PostgreSQL only and has no JSON fallback;
- restart/idempotency preserves the same journey and loop identities; and
- PostgreSQL mode writes no Lifecycle JSON generation or temporary files.

It does not edit Compose, deployment workflows, or legacy files. After it lands,
`LIFECYCLE-PROJ-RETIRE-001` remains the deployment-switch, restart/readback, and
exact-cleanup owner. The old task stays blocked until the root is present; a governed
audited operator correction then clears the stale seven-day/HMAC status blockers
without rewriting its immutable task payload. PR #5147 is not merged as-is.

### WP-02 — Candidate auto-binding, then bounded functional proof

Corrective root: `PFG-CANDIDATE-AUTO-BINDING-20260824`.

This task owns `scripts/cross_repo_release_controller.py`, its focused test, and the
Pantheon `.github/workflows/nonprod-deploy.yml` candidate-output contract. It derives
the immutable FE SHA, BFF SHA, and pair ID from the current candidate and served
manifest; rejects stale overrides; and preserves mismatch failure plus read-only
restoration. It does not edit `execute-plans` product or proof workflows.

After it lands, `PFG-BOUNDED-FUNCTIONAL-CLOSURE-PROOF-20260824` consumes the generated
candidate in its existing `execute-plans` workflows and performs the bounded proof.
The old task stays blocked until the root is present; a governed audited operator
correction then clears the stale old-pair/per-pair authorization status blockers
without changing its immutable task payload. Source remains reconcile-only and no
live-capital action is available.

### WP-03 — Hosted OpenClaw repair, then Management/AI journey

Corrective root: `PFG-MGMT-OPENCLAW-HOSTED-REPAIR-20260824`.

This task owns only the existing OpenClaw provider/adapter implementation and hosted
smoke path. It must produce a bounded real provider answer, preserve typed failures
when unreachable, and prove restart connectivity. It does not modify BFF main,
Compose/deployment, or frontend code and does not add a second provider endpoint.

`PFG-MGMT-JOURNEY-E2E-20260820` keeps ownership of the complete `execute-plans`
Management/AI journey. It consumes the provider repair and auto-bound proof window,
then proves real Formula, Activity, Paper Telemetry, and Postmortem reads; one actual
provider answer; one exactly-once paper-domain action; terminal readback; and reload
persistence with no fixture, seed, or prebuilt-ID shortcut.

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

Read-only sidecars:

- `PFG-BE-CONSOLIDATE-20260820-SIDECAR-CALLER-INVENTORY`; and
- `PFG-FE-CONSOLIDATE-20260820-SIDECAR-CALLER-INVENTORY`.

Each owns only its one `support/sidecars/.../caller-inventory-20260824.md` artifact and
enumerates imports, routes, workflows, deployment references, tests, docs, and runtime
callers. It classifies `retain`, `replace_then_delete`, `delete`, or `defer` without
changing product code, deployment, deletion, or canonical parent-task state.

Parent tasks `PFG-BE-CONSOLIDATE-20260820` and
`PFG-FE-CONSOLIDATE-20260820` keep all implementation and deletion authority. They
start only after their immutable declared dependencies are terminal, consume the
reviewed sidecars, and perform only replacement-proven changes.

Acceptance:

- no two active implementations own the same behavior without an explicit adapter
  boundary;
- no compatibility endpoint or frontend copy is added merely to keep old callers;
- affected unit/contract/journey tests pass after deletion; and
- the disposition ledger names replacement proof for every deletion.

### WP-06 — Final exact-pair hosted acceptance

Canonical task: `PFG-HOSTED-ACCEPT-20260820`.

This is the integration closure task, not an implementation bucket. It records the
served exact pair after the corrective roots/sidecars and their seven pre-existing
consumer tasks have produced the required evidence.

Acceptance:

- exact manifest and BFF version alignment;
- relational Lifecycle readiness and restart readback;
- complete Management/AI and Agora journey artifacts;
- consolidation validation;
- public read-only restoration; and
- Source reconcile-only confirmation.

## 5. Dependency graph and maximum parallelism

Canonical `depends_on` is immutable. All five newly materialized nodes declare only
`PFG-FUNCTIONAL-REAUDIT-DOCS-20260824`. The seven pre-existing rows retain their
original declared edges. The waves below are audited execution prerequisites and
handoff order; they do not pretend to rewrite those rows.

### Wave 0 — contract publication

Merge this four-document contract through
`PFG-FUNCTIONAL-REAUDIT-DOCS-20260824`.

### Wave A — five independent corrective lanes

1. `PFG-LIFECYCLE-POSTGRES-ACTIVATION-20260824`.
2. `PFG-CANDIDATE-AUTO-BINDING-20260824`.
3. `PFG-MGMT-OPENCLAW-HOSTED-REPAIR-20260824`.
4. `PFG-BE-CONSOLIDATE-20260820-SIDECAR-CALLER-INVENTORY`.
5. `PFG-FE-CONSOLIDATE-20260820-SIDECAR-CALLER-INVENTORY`.

Their artifacts do not overlap. The two sidecars are read-only and may not delete or
modify product code.

### Wave B — governed correction, deploy, and exercise

- after the Lifecycle root lands, apply the audited operator correction to the old
  retirement row, then let `LIFECYCLE-PROJ-RETIRE-001` own Compose/deploy switch,
  restart/readback, and exact JSON cleanup;
- after candidate auto-binding lands, apply the audited operator correction to the
  old bounded-proof row, then run its paper-only proof and watchdog restoration; and
- after the OpenClaw root and auto-bound candidate are ready, run the unchanged
  Management/AI and Agora journeys in parallel on the single leased candidate.

### Wave C — parent consolidation

After each parent task's declared dependencies are terminal, hand in its reviewed
sidecar and execute backend/frontend `replace_then_delete` or `delete` dispositions
in parallel. The parent, not the sidecar, owns every product change.

### Wave D — integration closure

- deploy the consolidated exact pair;
- rerun bounded acceptance where code identity changed;
- restore read-only; and
- complete `PFG-HOSTED-ACCEPT-20260820` last.

```text
docs contract
  +--> Lifecycle activation root --> old Lifecycle deploy/retire task ----+
  +--> candidate auto-binding root --> old bounded-proof task ------------+-->
  |                                  +--> Management/AI + Agora journeys --+   hosted
  +--> hosted OpenClaw repair -------+                                        accept
  +--> BE inventory sidecar --> existing BE consolidation ----------------+
  +--> FE inventory sidecar --> existing FE consolidation ----------------+
```

## 6. File ownership and conflict boundaries

| Task/lane | Exclusive owned artifacts | Exclusions |
|---|---|---|
| Lifecycle activation root | `services/trade_journey/lifecycle_projector.py`, `projection_store.py`, migrations/tests; BFF `trade_journey_projection_store.py`, `trade_journeys.py`, `main.py` | no Compose/deploy or file deletion |
| Existing Lifecycle retirement, later wave | `docker-compose.yml`, retirement/deploy integration, runbook/evidence; serial post-root cleanup of legacy branches in overlapping Lifecycle files | no concurrent edits with the activation root |
| Candidate auto-binding root | `scripts/cross_repo_release_controller.py`, `scripts/test_cross_repo_release_controller.py`, `.github/workflows/nonprod-deploy.yml` | no `execute-plans` code/workflows |
| Existing bounded proof | its three declared `execute-plans` proof workflows and evidence directory | no Pantheon controller ownership |
| Hosted OpenClaw repair root | `assistant_openclaw_provider.py`, adapter `main.py`, their two tests, and `scripts/openclaw-assistant-openclaw-live-smoke.sh` | no BFF main, Compose/deploy, or frontend |
| Existing Management journey | its two `execute-plans` E2E specs and evidence directory | no OpenClaw provider implementation |
| Existing Agora journey | existing Agora source/E2E/evidence surfaces | no parallel Agora implementation |
| BE inventory sidecar | Pantheon `support/sidecars/PFG-BE-CONSOLIDATE-20260820/caller-inventory-20260824.md` only | no product/deploy/delete/state change |
| FE inventory sidecar | `execute-plans` `support/sidecars/PFG-FE-CONSOLIDATE-20260820/caller-inventory-20260824.md` only | no product/deploy/delete/state change |
| Existing BE/FE consolidation parents | their already-declared product artifacts, after sidecar handoff and dependencies | no sidecar rewrite to hide product changes |
| Hosted acceptance | manifest/version verifier, runbook, and evidence only | no missing-feature implementation |

One file has one active owner per wave. The only intentional overlap is between the
Lifecycle root and later retirement task; it is serialized, with the retirement task
composing only after the root merges.

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

- all five corrective nodes and all seven pre-existing tasks are terminal with their
  identities and immutable history preserved;
- the hosted Lifecycle authority is relational and survives restart/readback;
- the exact legacy Lifecycle directory is removed and remains bounded;
- Management AI returns a real provider answer and Management/Agora write journeys
  pass with persisted reload evidence;
- all cleanup has caller-backed dispositions and no replacement duplication;
- the final hosted manifest identifies the tested exact pair; and
- public dev is read-only, Source is reconcile-only, and no live-capital action was
  exercised.
