# System Design — Development Tooling Four-Gap Closure

Status: implementation-ready design; package IDs are planning identifiers, not
canonical supervisor tasks.

## 1. Delivery rules

1. Every package starts from current `origin/dev` in a clean worktree.
2. DTG-CI-01 lands before code movement so later waves cannot evade tests.
3. Each extraction wave adds characterization tests before moving callers.
4. One integration lane owns edits to each monolith; parallel lanes may prepare
   target modules/tests but may not independently delete from the source file.
5. No package combines product-runtime changes with tooling cleanup.
6. Existing external command names, task transitions, lock order, runtime paths,
   and exit codes remain stable unless explicitly specified here.
7. A moved implementation is deleted from the source file in the same wave.
8. No execution task is materialized from this document until a separate
   operator request.

## 2. DTG-ARCH-01 design

### 2.1 Inventory

Record the immutable tuple from the live anchor in a custody worksheet:

```text
byte_size=9135812318
journal_sha256=185ba10f7d6d05a28d2b7bd53e8e13cb39a1297a25c249ddc7da5cfa063cc1bd
event_count=17135
last_event_sha256=b2aaa4cc71345ec63aad8ae507ccf450ce92efeff87d70ade3de181de2531f6b
state_sha256=ba2db008d4813600919bdc46667c49db0f4aee2f1a7c1b7a226377c3fbaebcb6
```

Search only authorized backup/custody locations. For every candidate, compare
file type and byte size before hashing 9.13 GB. Record path, storage owner,
observation time, size result, digest result, and disposition. Do not copy a
mismatching candidate into the runtime directory.

### 2.2 Recovery

If an exact candidate is found:

1. copy it to an operator-selected durable archive location using a temporary
   filename on the destination filesystem;
2. fsync and atomically rename;
3. make it non-world-readable and immutable by operational policy where
   supported;
4. run the existing verifier with `--archive-path`;
5. store the verifier JSON and custody manifest as documentation/evidence; and
6. leave the anchor unchanged.

The canonical runtime path need not be restored if the relocated path is the
declared custody location. No live config change is necessary because the
archive is verified only on explicit request.

### 2.3 Irrecoverable branch

If every authorized location is exhausted, create a factual disposition that
lists searched locations and preserves the expected tuple. Do not change the
verifier to green. Acceptance is “loss explicitly bounded,” not “archive
verified.”

### 2.4 Tests

Retain and extend verifier tests for:

- relocated exact candidate passes;
- wrong size fails without claiming digest match;
- correct size/wrong digest fails;
- symlink/non-regular candidate fails;
- absent candidate returns `historical_archive_unavailable`; and
- hot V2 projection remains independent.

## 3. DTG-CI-01 design

### 3.1 Classifier contract

Extend `classify_paths()` to return:

```json
{
  "development_tooling_touched": true,
  "product_touched": false,
  "delivery_touched": true,
  "tooling_only": false
}
```

`tooling_only` remains temporarily defined as development tooling touched and
neither product nor delivery touched. Add explicit manifest coverage for:

- `scripts/git/`
- `scripts/run-auto-integrator.sh`
- `scripts/auto_integrator_install.py`
- `scripts/supervisor_watchdog_install.py`
- the four-gap documentation directory as documentation, not runtime code.

Unknown paths remain reported. Branch CI must not derive development-tooling
ownership from “not product.”

### 3.2 Workflow gates

Replace the mutually exclusive workflow conditions with independent steps:

1. `Run tooling core gate` when `development_tooling_touched`.
2. `Run tooling integration authority gate` when
   `development_tooling_touched`.
3. `Run product smoke gate` when `product_touched`.
4. Run delivery checks already owned by the workflow when `delivery_touched`.

The integration authority command is:

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

Do not replace the existing core suite with this command; both are required.

### 3.3 Acceptance

- Classifier tests cover all domain combinations.
- A mixed synthetic diff selects both product and tooling commands.
- A `scripts/git/auto_integrator.py`-only diff selects tooling gates without
  relying on `unknown_paths`.
- Existing 1,026 tests and 7 subtests pass in the CI environment.
- The required-check name remains stable or branch protection is updated in
  the same delivery.

## 4. DTG-INT-01 design

### 4.1 Task contract

Add `integration_receipt` validation to the existing task contract. It is
optional and must not change role-based acceptance. Required fields are those
defined in SA ADR-03. SHA fields are lowercase 40-character Git OIDs, PR is a
positive integer, and `source` is exactly `canonical_auto_integrator`.

### 4.2 Governed command

Add a narrow internal command such as:

```text
ai-status record-integration-receipt TASK_ID \
  --generation N --repository OWNER/REPO --target-branch dev \
  --pr N --head-sha SHA --merge-commit-sha SHA --result merged
```

Admission rules:

1. caller runs from the promoted command runtime;
2. task exists and is `review_approved` or an allowed merge-then-review state;
3. generation and delivery binding exactly match;
4. actor/source identifies the canonical integration runner;
5. the command performs no GitHub call and trusts no unbound text evidence;
6. identical replay is a no-op; and
7. a conflicting receipt is rejected rather than overwritten.

The integrator supplies evidence only after its existing live GitHub and target
ancestry checks. The command commits through the existing TaskStore transaction
and activity outbox.

### 4.3 Candidate filter

Add a pure predicate:

```text
integration_receipt_consumes_candidate(task) -> bool
```

It returns true only for the complete exact identity. Candidate discovery then
omits the consumed row. It must return false for a malformed receipt, missing
delivery binding, changed generation, changed exact head, changed PR, changed
repository/target, or nonterminal receipt result.

### 4.4 Reopen/rebind behavior

`reopen`, `handoff`, operator reaccept, and any command that changes the frozen
delivery identity must either clear the receipt in the same canonical
transition or make it nonmatching by incrementing generation/changing binding.
Tests must prove the task becomes eligible again.

### 4.5 Crash windows

| Window | Result |
|---|---|
| before merge | no receipt; normal retry |
| merge succeeds, process dies before receipt | next cron verifies already merged and writes receipt |
| receipt commits, process dies before output | next cron filters exact receipt |
| receipt command rejects stale identity | no suppression; current candidate is evaluated |

### 4.6 Tests and live acceptance

- unit tests for receipt schema and predicate;
- command tests for idempotency/conflict and TaskStore persistence;
- integrator tests for performed merge and already-merged reconciliation;
- restart test proving the receipt survives a new process;
- generation/rebind invalidation tests;
- live canary with one already-merged `review_approved` task: first run records
  once, later cron runs produce no candidate/result line for that exact binding;
- task status and Human/Ops/owner finalization requirements remain unchanged.

## 5. DTG-CLEAN-01A monolith design

### 5.1 Wave M0 — characterization and dependency map

Before moving code, generate a checked review artifact listing every top-level
symbol in both monoliths, production callers, test callers, globals read/written,
external IO, locks acquired, and target owner. The artifact is migration
evidence, not a runtime registry.

Add tests that freeze:

- supervisor phase order and lock order;
- dispatch explanation parity;
- worker launch/recovery/requeue results;
- command names, arguments, exit codes, and actor restrictions;
- status/current-work/dashboard projections; and
- imports used by `scripts/explain_dispatch.py`,
  `scripts/prune_command_runtimes.py`, and `scripts/loop_done_guardrail.py`.

### 5.2 Wave M1 — projection-only status output

Move current-work and dashboard rendering, normalization needed only for those
views, and projection file writes from `ai_status.py` into one narrow status
projection module. It may read canonical snapshots but cannot mutate tasks.

Update tests to import the new owner directly. Remove the old bodies from
`ai_status.py`; do not leave forwarding copies after callers migrate.

### 5.3 Wave M2 — development bridge commands

Move signed packet verification, batch loading, dependency-closure validation,
materialization, and readback into the existing `development_bridge/` package.
`ai_status.py` retains CLI routing and opens the canonical transaction; bridge
code returns validated mutations/results.

The product BFF remains uninvolved.

### 5.4 Wave M3 — delivery and review evidence

Move pure delivery-binding, manifest, exact-head, and merged-evidence validation
to the existing task contract/review-gate ownership. Keep GitHub calls in
`scripts/git/github_review_bridge.py` and task mutation in the status command.
Delete duplicated parsing from `ai_status.py` after its command handlers call
the owner directly.

DTG-INT-01 should compose here rather than add another evidence module.

### 5.5 Wave M4 — worker workspace filesystem

Extract worktree preparation, reuse refresh, dirt classification, dirty archive,
registered lease cleanup, and orphan pruning into a narrow filesystem module.
It accepts explicit repository/worktree/config values and returns typed results;
it never reads or writes canonical task status.

Supervisor remains responsible for deciding when an operation occurs and for
persisting the returned lifecycle fact.

### 5.6 Wave M5 — recovery state transitions

Move pure receipt construction/validation and state transitions into the
existing `rewrite/worker_recovery.py` and lifecycle modules. Keep process
observation/termination and bounded external IO in the supervisor coordinator
until separated behind explicit operation results.

There must still be one recovery fence and one durable receipt schema.

### 5.7 Wave M6 — dispatch planning

Move candidate evaluation, capacity accounting, assignment pair selection, and
dispatch explanation to existing dispatch policy/admission owners. Both live
dispatch and `scripts/explain_dispatch.py` call the same pure decision function.

Supervisor keeps snapshot loading, reservation transaction, worker start, and
result persistence. No scheduler class or alternate run loop is introduced.

### 5.8 Wave M7 — cycle composition finish

Reduce `run_once` to an explicit ordered phase table or short coordinator.
`supervisor.py` retains process signals, singleton acquisition, configuration,
phase invocation, and lifecycle reporting. `ai_status.py` retains path binding,
CLI parser/routing, transaction opening, and final projection invocation.

Guardrails at completion:

- `supervisor.py` no longer owns domain implementations for bridge, worktree,
  recovery policy, provider policy, or dispatch policy;
- `ai_status.py` no longer owns projection rendering, bridge protocol, or
  delivery-evidence parsing;
- no extracted module exceeds the original monolith by becoming a generic
  replacement;
- no circular production import is introduced;
- no forwarding-only module remains;
- all direct production imports are documented and point to the canonical
  owner; and
- source size is materially reduced, with review required if either entrypoint
  remains above 4,000 lines.

The line threshold is a review trigger, not permission to split code
mechanically. Responsibility acceptance takes precedence.

## 6. DTG-CLEAN-01B evidence design

### 6.1 Inventory schema

Generate a non-runtime report with one row per candidate historical brief:

```json
{
  "path": ".orchestrator/task-briefs/...md",
  "task_id": "...",
  "canonical_status": "done|superseded|missing|active",
  "archive_snapshot": "path or null",
  "review_references": [],
  "delivery_references": [],
  "documentation_references": [],
  "content_sha256": "...",
  "disposition": "KEEP|MIGRATE|REMOVE|VERIFY",
  "reason": "..."
}
```

### 6.2 Disposition rules

- **KEEP:** active task, exact review/delivery binding, immutable incident
  evidence, or unresolved reference.
- **MIGRATE:** only when an existing canonical archive owner can retain the
  exact bytes and every live caller can move atomically.
- **REMOVE:** zero caller/reference, terminal canonical task, exact content
  already held by an accepted canonical archive, and no verifier requires the
  original path.
- **VERIFY:** missing/corrupt task identity, ambiguous cross-repository path,
  or evidence mismatch.

`integration_unblock_` and `sidecar` in a filename are not REMOVE criteria.

### 6.3 Existing retention owner

Keep `sidecar_cleanup.py` and `evidence_retention_policy.py` while
`support/sidecars/` retention uses them. Retire those files only after their
production callers are zero and a replacement is not required. Lock sidecars
in `common.py`/`runtime_state.py` are unrelated and stay.

### 6.4 Prevention

Add a focused test or linter that rejects new task metadata using retired
sidecar scheduler fields/classes. It must allow historical fixtures explicitly
marked as legacy and ordinary filesystem lock-sidecar terminology.

### 6.5 Acceptance

- all 815 candidates have a disposition and reason;
- no active/review-bound file is removed;
- every REMOVE row has zero-reference and archive-content proof;
- git diff contains only the exact approved removals/migrations;
- task archive/review verification still resolves every retained path; and
- supervisor, status, bridge, review, and integrator suites pass afterward.

## 7. Planning package DAG

These are planning packages, not execution tasks:

| Package | Scope | Dependencies | Parallelism |
|---|---|---|---|
| DTG-CI-01A | classifier independent predicates and manifest paths | none | parallel with archive inventory |
| DTG-CI-01B | branch workflow authority suite | CI-01A | short serial integration |
| DTG-INT-01A | receipt contract, command, predicate tests | CI-01B | independent of monolith inventory |
| DTG-INT-01B | integrator write/filter/restart canary | INT-01A | serial exact-owner integration |
| DTG-CLEAN-M0 | symbol/caller/lock characterization | CI-01B | can run beside INT-01 |
| DTG-CLEAN-M1 | status projection extraction | CLEAN-M0 | parallel preparation |
| DTG-CLEAN-M2 | bridge extraction | CLEAN-M0 | parallel preparation |
| DTG-CLEAN-M3 | delivery/review evidence extraction | INT-01B, CLEAN-M0 | composes receipt |
| DTG-CLEAN-M4 | worktree extraction | CLEAN-M0 | parallel preparation |
| DTG-CLEAN-M5 | recovery extraction | CLEAN-M0 | parallel preparation |
| DTG-CLEAN-M6 | dispatch decision extraction | CLEAN-M0 | parallel preparation |
| DTG-CLEAN-M7 | entrypoint/cycle finish | CLEAN-M1–M6 | serial final integration |
| DTG-CLEAN-E1 | evidence inventory only | none | parallel with all code preparation |
| DTG-CLEAN-E2 | proven retention actions | CLEAN-E1, CLEAN-M0 | independent bounded batch |
| DTG-ARCH-01A | authorized custody search | none | fully independent |
| DTG-ARCH-01B | exact recovery or loss disposition | ARCH-01A | external-data dependent |

Parallel lanes prepare target files and tests. The integration lane serializes
deletions from `supervisor.py` and `ai_status.py` to avoid overlapping edits.

## 8. Full validation matrix

| Area | Required validation |
|---|---|
| TaskStore | hot parity, full V2 replay, explicit archive audit classification |
| Supervisor | singleton, phase order, runtime health, process recovery E2E |
| Dispatch | candidate matrix, capacity/fallback, explain/live decision parity |
| Worker | launch, heartbeat, completion, lost lease, reassignment, worktree cleanup |
| Review | handoff manifest, exact head, reopen/requeue, role acceptance |
| Integration | lock ownership, no auto-merge, exact-head merge, already-merged receipt, restart |
| Bridge | signature, admission, replay rejection, dependency closure, receipt/readback |
| CI | tooling/product/delivery/mixed classifier fixtures and named suites |
| Evidence | reference inventory, archive resolution, zero-reference deletion proof |
| Live canary | official health all dimensions green; one dispatch; one merge receipt; later cron suppression |

## 9. Rollback

- CI predicate changes revert as one workflow/classifier commit if they
  incorrectly suppress a required gate; never merge a bypassing predicate.
- Receipt code can stop filtering while retaining receipts as inert evidence.
  Do not delete or rewrite canonical receipts during rollback.
- Each extraction wave is independently revertible because it moves one
  responsibility and its callers together.
- Archive recovery has no runtime rollback. A copied candidate is accepted only
  after exact verification; a rejected candidate is quarantined outside runtime.
- Evidence deletions require a recoverable git commit and exact inventory; no
  broad filesystem deletion is part of this design.
