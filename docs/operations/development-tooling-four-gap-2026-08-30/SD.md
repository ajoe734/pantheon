# Complete System Design — Development Tooling Four-Gap Closure

Status: execution-ready design for one continuous Claude implementation run

Baseline: `origin/dev` at or after
`9c9adf426f04276d1b1a0a1401eb1f81bc0ebec4`

This document is normative for implementation. Where an older statement in
`SA.md`, `GAP_AUDIT.md`, or `THREE_PASS_REVIEW.md` conflicts with this
file, this file wins. Canonical mutation means use of the single V2 TaskStore
transaction/projection contract; it does not mean every internal tooling writer
must impersonate a leased worker through the public `ai-status` CLI.

## 1. Objective

Close the four residual development-tooling gaps without adding a second
scheduler, merge lane, task store, status authority, runtime cache, or generic
facade:

1. `DTG-ARCH-01`: recover and verify the immutable V1 journal if exact bytes
   exist, or produce an honest bounded-loss disposition.
2. `DTG-CI-01`: make CI select every applicable tooling/product gate for a
   changed-path set, including mixed diffs.
3. `DTG-INT-01`: persist exact integration consumption so an already-landed
   delivery is not re-evaluated every five minutes.
4. `DTG-CLEAN-01`: reduce the two development-tooling monoliths by moving
   behavior to canonical owners and classify historical brief evidence by
   proof rather than filename.

The implementation is complete only when all source changes, migrations,
deletions, tests, documentation, and live canaries in this document pass. A
local refactor or green unit subset is not completion.

## 2. Scope and explicit non-goals

### 2.1 In scope

- `.orchestrator/supervisor.py` and its development-tooling owner modules.
- `scripts/ai_status.py` and its canonical task/projection owner modules.
- `scripts/git/auto_integrator.py` and its focused tests/contract.
- `scripts/component_boundary.py`, component-boundary manifest, and branch CI.
- V2 task contract/TaskStore code required for `integration_receipt`.
- Development-tooling tests and documentation.
- Read-only inventory of historical task briefs and proven, separately
  reviewed removals.
- Offline V1 archive recovery verification.

### 2.2 Out of scope

- Product BFF, twelve-loop, Management UI, frontend, product database, product
  readiness, production deployment, trading, or capital-affecting behavior.
- Security-hardening programs, permission redesign, secret rotation, archive
  encryption, immutable filesystem flags, or custody infrastructure.
- A new scheduler, queue, TaskStore, merge process, bridge, worker role, task
  class, generic mutation command, or product API endpoint.
- Automatic Human/Ops or owner finalization after merge.
- Synthetic reconstruction of a missing V1 archive.
- Mechanical file splitting solely to reach a line-count target.
- Blanket deletion based on `sidecar` or `integration_unblock` names.

## 3. Required starting conditions

The executor must stop before editing if any condition is false:

1. Fetch `origin` and create a clean worktree/branch from current
   `origin/dev`.
2. Record the baseline SHA in delivery evidence.
3. Confirm no live worker owns the same files through a current task.
4. Confirm live supervisor health is green using the promoted command runtime
   and exact live config, not a shared checkout.
5. Confirm the working tree is clean.

Do not use `/home/lupin/pantheon` dirty/shared state as source truth. Do not
edit canonical task JSON, queue JSON, activity logs, or V2 journals by hand.

## 4. Canonical architecture and invariants

### 4.1 Authority map

| Concern | Canonical owner |
|---|---|
| task state and transition journal | V2 TaskStore |
| human/worker task command CLI | `scripts/ai_status.py` |
| scheduler/cycle/singleton | `.orchestrator/supervisor.py` |
| merge execution | `scripts/git/auto_integrator.py` |
| dispatch policy | `.orchestrator/dispatch_policy.py` and `.orchestrator/rewrite/dispatch_admission.py` |
| worker recovery policy | existing `.orchestrator/rewrite/worker_recovery.py` and lifecycle modules |
| signed development packet transport | `.orchestrator/development_bridge/` |
| GitHub review probes | existing Git review-gate/bridge modules |
| derived current-work/dashboard files | one narrow status projection owner |
| changed-path classification | `scripts/component_boundary.py` plus manifest |

### 4.2 Invariants

1. There is one V2 canonical task-state journal and one derived projection.
2. Every canonical mutation commits through the existing V2 TaskStore
   transaction and updates derived views through the existing projection
   contract.
3. Public `ai-status` worker commands retain active-lease validation.
4. Internal supervisor/integrator mutations use narrow purpose-specific
   functions with their own authority checks; they do not fake a worker lease
   or enable `PANTHEON_LOCAL_HUMAN_OPS`.
5. The supervisor remains one singleton and `run_once` remains the sole cycle
   coordinator.
6. The auto-integrator remains the sole merge owner and retains one canonical
   flock.
7. Workers, PR helpers, and product runtime never merge or mutate integration
   receipts.
8. Runtime `state.json` remains bounded operational state, never canonical
   delivery truth.
9. Every extraction wave moves callers and deletes the replaced body in the
   same commit. No permanent forwarding facade is accepted.
10. External command names, exit codes, task transitions, lock ordering,
    runtime paths, and exact-head review rules remain compatible unless this
    design explicitly changes them.

## 5. DTG-CI-01 — independent CI gate selection

### 5.1 Root defect

`scripts/component_boundary.py` currently derives:

```text
product_touched = product_runtime in domains
tooling_only = changed paths exist and product_touched is false
```

Branch CI then chooses tooling or product smoke with mutually exclusive
conditions. A mixed product/tooling diff therefore skips the tooling authority
suite. Unknown-only and delivery-only diffs can also be mislabeled tooling.

### 5.2 Files owned by this change

- `scripts/component_boundary.py`
- `scripts/test_component_boundary.py`
- `docs/02-architecture/component-boundary.yaml`
- `.github/workflows/branch-ci.yml`
- focused workflow tests already present in the repository, if required

### 5.3 Classifier contract

Add these independent booleans:

```json
{
  "development_tooling_touched": true,
  "product_touched": true,
  "delivery_touched": false,
  "tooling_only": false
}
```

Rules:

- `development_tooling_touched` is true only when a matched component has the
  `development_tooling` domain.
- `product_touched` is true only when a matched component has the
  `product_runtime` domain.
- `delivery_touched` is true only when a matched component has the
  `delivery` domain.
- Keep legacy `tooling_only` unchanged during this delivery:
  `bool(paths) and not product_touched`.
- Branch CI must stop using `tooling_only`; deprecation requires a separate
  caller inventory.
- Unknown paths remain listed. They do not silently become development
  tooling.
- Do not add a documentation domain in this change.

Add manifest coverage for executable tooling paths currently omitted:

- `scripts/git/`
- `scripts/run-auto-integrator.sh`
- `scripts/auto_integrator_install.py`
- `scripts/supervisor_watchdog_install.py`
- associated focused test files

Do not classify this documentation directory as runtime code.

### 5.4 Workflow behavior

Replace mutually exclusive smoke selection with independent steps:

1. Run existing tooling core gate when
   `development_tooling_touched == true`.
2. Run tooling integration-authority gate when
   `development_tooling_touched == true`.
3. Run existing product smoke when `product_touched == true`.
4. Preserve existing delivery checks. Do not invent a new delivery program in
   this scope; `delivery_touched` is exposed for explicit callers.
5. Unknown-only diffs retain the repository's conservative/default smoke
   behavior; there must be no successful no-test path.

The tooling integration-authority gate is exactly:

```bash
PYTHONPATH=.orchestrator python3 -m pytest -q \
  scripts/git/test_auto_integrator.py \
  scripts/git/test_task_review_merge_gate.py \
  scripts/git/test_github_review_bridge.py \
  scripts/test_run_auto_integrator.py \
  scripts/test_auto_integrator_install.py \
  scripts/test_supervisor_launch_ownership.py \
  scripts/test_supervisor_watchdog_install.py
```

It supplements, not replaces, the existing tooling core gate.

### 5.5 Required tests

Add table-driven classifier tests for tooling-only, product-only,
delivery-only, every two-domain pair, all three domains, docs-only,
unknown-only, and `scripts/git/auto_integrator.py` alone.

Add a workflow assertion proving a mixed tooling/product diff selects both
tooling gates and product smoke. Preserve the required check name unless branch
protection is changed in the same tooling delivery.

### 5.6 Acceptance

- No applicable gate is skipped for any tested domain union.
- No executable `scripts/git/` integrator change is classified unknown.
- Existing legacy callers of `tooling_only` receive the old value.
- Tooling core, integration-authority, and product smoke commands pass.

## 6. DTG-INT-01 — exact integration consumption receipt

### 6.1 Root defect

Candidate discovery selects every eligible `review_approved` row. After a PR
is already merged, the integrator returns `already_merged` but persists no
consumption. The five-minute cron repeats GitHub/ancestry work indefinitely.

A normal `ai-status` command is not a valid write path for the cron:
public mutation admission requires an active worker lease or explicit local
Human/Ops mode. The integrator has neither and must not impersonate either.

### 6.2 Canonical receipt schema

Add one optional row-bound field to the existing task contract:

```json
{
  "integration_receipt": {
    "version": 1,
    "result": "landed",
    "observation": "performed_merge",
    "task_generation": 4,
    "repository": "ajoe734/pantheon",
    "target_branch": "dev",
    "pr": 5411,
    "head_sha": "254d2e7b05096dad3f6c7512db089ae2cbd8fe08",
    "merge_commit_sha": "8f8383b507b1fb631d44422031f01ebea5024d5e",
    "observed_at": "2026-08-29T23:05:12Z",
    "source": "canonical_auto_integrator"
  }
}
```

Contract:

- Receipt is bound to the containing row; do not duplicate `task_id`.
- `version` is integer 1 and `result` is exactly `landed`.
- `observation` is `performed_merge` or
  `reconciled_existing_merge`.
- Generation equals the current row; repository/branch equal frozen delivery.
- PR is positive; SHAs are lowercase 40-character Git OIDs.
- Timestamp is UTC RFC3339; source is `canonical_auto_integrator`.
- Unknown versions or malformed receipts are non-consuming evidence and must
  not make the canonical projection unreadable.

### 6.3 Receipt identity

The consuming identity is:

```text
(task row id, task generation, repository, target branch, PR number,
 exact approved head SHA, merge commit SHA, result=landed)
```

`observation`, timestamp, and source are audited fields, not alternate
identity.

### 6.4 Internal mutation owner

Implement one narrow internal mutation function in the existing canonical
task-state/status owner. Do not add a general task amendment API.

Suggested interface:

```python
record_integration_receipt(
    *,
    config: Mapping[str, Any],
    task_id: str,
    expected_generation: int,
    expected_delivery_binding: IntegrationBinding,
    receipt: IntegrationReceipt,
    authority: IntegrationAuthority,
) -> ReceiptWriteResult
```

Use an existing task-contract/status module, or one narrow
`integration_receipt.py` if no existing owner can hold schema, comparison,
and mutation without a circular import. It must not become a generic status
service or facade.

Authority validation must prove:

1. source is the promoted command runtime recorded by live config;
2. status root is the canonical absolute status root;
3. canonical auto-integrator flock is held by the current process and owner
   metadata matches the current process generation;
4. row, generation, repository, branch, PR, and approved head still match the
   revalidated delivery snapshot;
5. status remains `review_approved` or the explicitly supported
   merge-then-review state; and
6. mutation uses existing TaskStore transaction, activity audit, and derived
   projection paths.

Do not set `ORCH_RUN_ID`, create a fake lease, set
`PANTHEON_LOCAL_HUMAN_OPS`, or trust the literal source string.

Lock order remains:

```text
auto-integrator flock
  -> existing task-state transaction lock
  -> existing activity/projection locks in their current order
```

No runtime-state lock is added.

### 6.5 Write semantics

- Successful merge: verify GitHub state, exact head, merge commit, and target
  ancestry; write `performed_merge`.
- Already merged: perform the same review/ancestry checks; write
  `reconciled_existing_merge`.
- Identical replay is no-op success.
- Conflicting non-empty receipt is rejected, never overwritten.
- Merge success plus receipt failure leaves the task `review_approved`; next
  cron reconciles it.
- Receipt never finalizes the task or bypasses owner/Human-Ops closeout.

### 6.6 Candidate filter

Implement a pure row predicate:

```python
integration_receipt_consumes_candidate(task) -> bool
```

It compares only the canonical row and frozen delivery binding. It performs no
GitHub call, fetch, filesystem operation, or ancestry query.

Return false for missing/malformed/unknown-version receipt, non-`landed`
result, changed generation/repository/branch/PR/head, missing merge commit, or
incomplete current delivery binding.

Target ancestry is proved before receipt creation. History-rewrite monitoring
belongs to delivery-integrity diagnostics, not this per-cron predicate.

### 6.7 Reopen and rebind

Inventory every command that can change generation or frozen delivery. For
each, prove either that it atomically clears the receipt or makes it nonmatching
by changing generation/binding.

At minimum cover `reopen`, `handoff`, operator reaccept, PR rebind,
exact-head change, repository change, and target-branch change.

### 6.8 Required tests

- schema accept/reject and pure identity matrices;
- exact replay no-op and conflicting receipt rejection;
- TaskStore journal and derived projection persistence;
- performed merge and already-merged reconciliation;
- crash before merge, after merge/before receipt, after receipt/before output;
- process restart suppression;
- generation and every delivery-binding invalidation;
- public worker lease checks remain unchanged;
- integrator cannot write without canonical flock/runtime identity; and
- task remains `review_approved` after receipt.

### 6.9 Live canary

With one already-merged `review_approved` exact binding:

1. capture task row and cron log position;
2. run canonical integrator once;
3. verify exactly one V2 transition and projection receipt;
4. allow at least two scheduled cron cycles;
5. prove the exact candidate/result line no longer appears;
6. prove finalization authority/status is unchanged; and
7. require official supervisor health all green.

## 7. DTG-CLEAN-01A — canonical-owner decomposition

### 7.1 Success definition

Success is one canonical owner per behavior, all callers migrated, old bodies
removed, no new circular import, and stable external behavior. File size alone
is not acceptance.

Entrypoints remain:

- `.orchestrator/supervisor.py`: config/bootstrap, singleton, ordered cycle,
  external-operation orchestration, lifecycle reporting.
- `scripts/ai_status.py`: parsing/routing, public actor admission,
  transaction opening, final projection invocation.

### 7.2 Mandatory M0 disposition

Create
`docs/operations/development-tooling-four-gap-2026-08-30/MONOLITH_SYMBOL_DISPOSITION.json`
with one row for every top-level class/function in both monoliths:

```json
{
  "source": ".orchestrator/supervisor.py",
  "symbol": "example",
  "line": 123,
  "production_callers": [],
  "test_callers": [],
  "globals_read": [],
  "globals_written": [],
  "external_io": [],
  "locks": [],
  "disposition": "KEEP|MIGRATE|MERGE|REMOVE|VERIFY",
  "target_owner": "module.symbol or null",
  "wave": "M1..M7",
  "reason": "..."
}
```

Explicitly include production imports from `scripts/explain_dispatch.py`,
`scripts/prune_command_runtimes.py`, and
`scripts/loop_done_guardrail.py`.

M1 may not begin if any symbol lacks a disposition, a MIGRATE/MERGE row lacks
an owner, two targets claim one authority, a lock-bearing function lacks order
notes, a production caller is unresolved, or a production import cycle would
result. M0 is evidence; production code must not load it.

### 7.3 Rules common to M1–M7

Every wave:

1. adds characterization tests;
2. moves implementation to the canonical owner;
3. migrates all production/test callers;
4. deletes the old body in the same commit;
5. adds no forwarding-only compatibility module;
6. runs focused plus core supervisor/status suites;
7. records final owner/commit in M0 evidence; and
8. stops if parity requires behavior changes outside this design.

Only one integration lane edits a monolith at a time. One Claude run performs
the waves sequentially.

### 7.4 M1 — derived status projection

Move current-work/dashboard rendering, view-only normalization, and projection
file writes into one narrow status projection module. It may read snapshots
and write derived views; it cannot mutate tasks or implement command policy.

Require semantic/byte parity fixtures, unchanged failure/atomic-write behavior,
and deletion of duplicate renderers from `ai_status.py`.

### 7.5 M2 — development bridge commands

Move signed packet verification, batch loading, dependency closure,
materialization planning, and readback into existing
`.orchestrator/development_bridge/`.

`ai_status.py` retains CLI routing, public authority admission, and canonical
transaction boundary. Bridge code returns validated mutations/results and
never exposes a product BFF route.

Require signature, replay rejection, dependency closure, materialization, and
readback tests; remove duplicate protocol bodies.

### 7.6 M3 — delivery and review evidence

Move pure delivery binding, exact-head, manifest, review, and merged-evidence
validation to existing task-contract/review-gate owners. Keep GitHub calls in
existing bridge/integrator modules and canonical mutation in TaskStore/status
ownership. Compose Section 6 here; do not add a second evidence module.

Require unchanged role/exact-head behavior, removal of duplicate parsers, and
no GitHub calls inside task contract validation.

### 7.7 M4 — worker workspace filesystem

Move worktree preparation, safe reuse/refresh, dirt classification, dirty
archive, registered-lease cleanup, and orphan pruning into one narrow workspace
filesystem owner if no existing owner fits.

It accepts explicit inputs and returns typed results. It never reads/writes
canonical tasks or decides dispatch. Supervisor retains timing and persistence.

Require clean reuse, dirty refusal/archive, lease-owned cleanup, orphan cleanup,
and path-boundary tests. Add no worktree registry or lease store.

### 7.8 M5 — worker recovery transitions

Move pure lost-lease receipt construction/validation and recovery transitions
into existing worker recovery/lifecycle owners. Keep process observation,
termination, and bounded external I/O in supervisor until modeled as explicit
operation inputs/results.

Require one durable lost-lease receipt, atomic/idempotent reassignment, stale
generation fencing, and removal of duplicate recovery policy.

### 7.9 M6 — dispatch planning

Move pure candidate evaluation, dependency eligibility, capacity accounting,
agent/provider selection, and explanation to existing dispatch
policy/admission owners.

Live dispatch and `scripts/explain_dispatch.py` use the same pure decision.
Supervisor retains snapshot load, reservation transaction, worker launch, and
result persistence.

Require explain/live parity, capacity/fallback, claim helper, dependency, and
same-cycle reservation tests. Add no scheduler or alternate run loop.

### 7.10 M7 — composition finish

Reduce `run_once` to a short explicit coordinator invoking named phases in the
existing order. Do not add a data-driven phase framework unless it removes real
duplication.

Supervisor retains signals, singleton, config/dependencies, transaction
orchestration, phase invocation, and lifecycle reporting.

ai-status retains parser/routing, path/runtime binding, public actor/lease
admission, transaction opening, and final projection invocation.

Completion review requires one owner per concern, no duplicate/forwarding-only
module, no new import cycle, documented production imports, and material source
reduction attributable to moved behavior. Remaining files above 4,000 lines
require an ownership explanation; 4,000 lines is not a failure by itself.

## 8. DTG-CLEAN-01B — historical evidence disposition

Generate a non-runtime inventory for every candidate brief:

```json
{
  "path": ".orchestrator/task-briefs/example.md",
  "git_tracked": true,
  "task_id": "...",
  "canonical_status": "done|superseded|missing|active",
  "archive_snapshot": "path or null",
  "review_references": [],
  "delivery_references": [],
  "documentation_references": [],
  "runtime_callers": [],
  "content_sha256": "...",
  "disposition": "KEEP|MIGRATE|REMOVE|VERIFY",
  "reason": "..."
}
```

- KEEP: active, exact evidence, runtime caller, or unresolved reference.
- MIGRATE: exact bytes fit an existing archive and all callers move atomically.
- REMOVE: terminal, zero reference/caller, exact archive copy, no verifier path
  dependency.
- VERIFY: ambiguous/corrupt identity or unresolved cross-repository evidence.

Names alone are never removal evidence. Keep `sidecar_cleanup.py` and
`evidence_retention_policy.py` while `support/sidecars/` uses them.
Filesystem lock-sidecars are unrelated.

Do not add a prevention linter unless M0 proves new materialization can still
emit a retired scheduler class. If proven, validate only at the existing
materialization boundary and allow explicit historical fixtures.

Inventory and removal are separate commits. Before removal, recheck exact-path
references from current `origin/dev`, prove archive equality, run
task/review/delivery/evidence verifiers, and remove only the reviewed exact
list. Any changed evidence downgrades the row to VERIFY.

## 9. DTG-ARCH-01 — offline V1 archive disposition

Use the live anchor tuple:

```text
byte_size=9135812318
journal_sha256=185ba10f7d6d05a28d2b7bd53e8e13cb39a1297a25c249ddc7da5cfa063cc1bd
event_count=17135
last_event_sha256=b2aaa4cc71345ec63aad8ae507ccf450ce92efeff87d70ade3de181de2531f6b
state_sha256=ba2db008d4813600919bdc46667c49db0f4aee2f1a7c1b7a226377c3fbaebcb6
```

Search only authorized backup/custody locations. Record candidate path, type,
size, observation time, digest, and disposition.

If exact bytes exist, copy to an operator-selected durable path through a
temporary destination, fsync and atomically rename, run the existing verifier
with `--verify-archive --archive-path`, retain verifier JSON/custody facts,
and leave anchor/hot V2 config unchanged.

Do not add permission hardening, encryption, immutable flags, daemon, startup
check, fallback reader, or live configuration dependency.

If exact bytes do not exist, retain `historical_archive_unavailable` and
document exhausted locations. Never synthesize bytes or weaken verification.
Archive work is independent and cannot block healthy hot tooling delivery.

## 10. One-run implementation order

Claude performs this on one task branch:

1. Capture baseline and pre-change focused tests.
2. Implement and validate DTG-CI-01.
3. Implement receipt schema, predicate, mutation authority, and tests.
4. Integrate receipt write/filter into auto-integrator.
5. Complete M0 disposition.
6. Execute M1, M2, M4, M5, and M6 sequentially.
7. Execute M3 after receipt ownership is stable.
8. Execute M7.
9. Generate evidence inventory; perform no deletion before its gate.
10. Perform only proven migrations/removals.
11. Run the full validation matrix.
12. Rebase/merge current `origin/dev`; rerun conflict-affected tests.
13. Commit, push, and deliver through the development-tooling flow.
14. Promote the exact merged commit and run live receipt/health canaries.
15. Perform archive search independently when authorized storage is available.

Multiple commits are rollback boundaries, not permission to leave duplicate
bodies or partially migrated callers in the delivered result.

## 11. Recommended commits

1. `DTG-CI-01: select independent tooling and product gates`
2. `DTG-INT-01: persist canonical integration receipts`
3. `DTG-CLEAN-M0: record monolith symbol ownership`
4. one commit per M1–M6 wave
5. `DTG-CLEAN-M7: finish tooling entrypoint composition`
6. `DTG-CLEAN-E1: classify historical brief evidence`
7. `DTG-CLEAN-E2: remove proven redundant evidence` only with REMOVE rows
8. `DTG-ARCH-01: record archive recovery disposition` only when performed

Every commit uses required trailers and contains no product change.

## 12. Full validation matrix

Run the existing core tooling gate and:

```bash
PYTHONPATH=.orchestrator python3 -m pytest -q \
  scripts/git/test_auto_integrator.py \
  scripts/git/test_task_review_merge_gate.py \
  scripts/git/test_github_review_bridge.py \
  scripts/test_run_auto_integrator.py \
  scripts/test_auto_integrator_install.py \
  scripts/test_supervisor_launch_ownership.py \
  scripts/test_supervisor_watchdog_install.py
```

Run all focused suites changed by M1–M7: supervisor, ai-status, TaskStore,
runtime state, bridge, recovery, worktree, dispatch, review, loop guardrail,
command runtime, container/entrypoint, and component boundary.

| Area | Required proof |
|---|---|
| TaskStore | hot parity, journal append, projection, full V2 replay |
| Supervisor | singleton, phase/lock order, health, recovery E2E |
| Dispatch | candidate/capacity/fallback, claim helper, explain/live parity |
| Worker | launch, heartbeat, done, lost lease, reassignment, cleanup |
| Review | handoff, exact head, reopen/requeue, role acceptance |
| Integration | sole lock owner, merge, receipt, restart suppression |
| Bridge | signature, replay rejection, dependency closure, readback |
| CI | all domain combinations and mixed union |
| Evidence | complete dispositions and zero-reference deletion proof |
| Deployment | exact command runtime and container/entrypoint tests |

After exact merged tooling commit promotion, require:

- official supervisor health all green;
- promoted command root SHA equals delivered tooling SHA;
- one normal dispatch/worker lifecycle completes;
- one already-merged canary records once;
- two later cron cycles omit that exact candidate;
- finalization authority remains unchanged;
- no new supervisor/integrator/scheduler/queue/TaskStore path appears; and
- no lease-bypass attempt or repeated receipt conflict appears in logs.

## 13. Failure and rollback

- CI regression: revert classifier/workflow together.
- Receipt failure before merge: no receipt; retry normally.
- Merge succeeds before receipt failure: remain review-approved; reconcile next
  cron.
- Conflicting receipt: stop and diagnose; never overwrite.
- Extraction parity failure: revert that wave; do not add a facade.
- Import cycle: restore previous owner and redesign the wave.
- Evidence uncertainty: KEEP/VERIFY.
- Archive mismatch/absence: preserve anchor; hot V2 remains unaffected.
- Live regression: roll back prior promoted command runtime and retain failed
  exact-SHA evidence.

## 14. Definition of done

All are mandatory:

- independent CI predicates and mixed gates merged;
- canonical receipt persists and suppresses repeat evaluation;
- no fake lease or Human/Ops bypass;
- task finalization authority unchanged;
- M0 covers every monolith symbol;
- each executed wave has one owner, migrated callers, deleted old body;
- no duplicate scheduler/queue/store/merge lane/facade/import cycle;
- evidence inventory complete and only proven rows removed;
- archive exactly verified or honestly unavailable;
- all local/repository checks pass;
- exact merged tooling commit promoted;
- live health and receipt canaries pass; and
- evidence records baseline/final SHA, commits, commands, results, runtime
  identity, canary observations, and retained VERIFY items.

If any item is missing, report the exact blocker and leave the work incomplete.
Do not reinterpret acceptance or generate repair layers over an unproven
design.
