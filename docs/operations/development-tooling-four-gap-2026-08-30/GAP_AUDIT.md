# Development Tooling Four-Gap Audit

Audit date: 2026-08-30 UTC

Source baseline: `origin/dev` at
`e7f010dccee33185bc260d06048f09e6d2125f28`

Live tooling runtime: `954caefa519ab89827b4d3030a511f2f7c73138a`
(PR #5419). No development-tooling source path changed between the live runtime
and the source baseline.

## 1. Method and completion boundary

The audit used three separate passes:

1. current code, live configuration, live state, process, timer, cron, and
   GitHub delivery evidence;
2. callers, canonical owners, duplicate-path risk, deletion safety, and
   existing test ownership; and
3. reverse trace from acceptance criteria to code and tests, followed by a
   conflict review against Supervisor Authority V2.

Passing unit tests alone was not treated as live evidence. Historical task
records alone were not treated as code truth. The audit did not mutate runtime
state or product code.

## 2. Current healthy baseline

The four residual gaps do not invalidate the current hot-path baseline:

| Capability | Current result |
|---|---|
| Supervisor identity, singleton and heartbeat | healthy |
| V2 TaskStore head/projection parity | healthy; 9,184 events at observation |
| Dispatch and worker launch | live worker observed |
| Lost-lease recovery | typed receipts present; 64 retained receipts |
| Worktree cleanup | `missing_leases=0`, `failed=0` |
| Assistant development bridge | no pending root packet; last drain succeeded |
| Queue retention | 201 rows; 1,828 terminal rows compacted |
| Canonical integration runner | live cron; exact-head PR #5426 merge observed |
| Watchdog | systemd timer enabled and active; last oneshot result success |

The focused audit suite passed 1,026 tests and 7 subtests. This proves the
checked implementation, but Gap CI-01 below explains why part of that suite is
not yet a required branch gate.

## 3. Gap summary

| ID | Finding | Runtime impact | Disposition |
|---|---|---|---|
| DTG-ARCH-01 | Exact immutable V1 journal bytes are unavailable | no hot-path impact; full V1 historical audit cannot pass | recover exact bytes or record an explicit irrecoverable-data disposition; never synthesize |
| DTG-CI-01 | Branch CI omits integration-authority suites and skips tooling tests for mixed product/tooling diffs | regression can merge without the relevant gate | fix component predicates and add a named authority suite |
| DTG-INT-01 | `already_merged` is recomputed every cron run because no canonical consumption receipt is persisted | repeated GitHub calls/log noise; no incorrect merge | add one narrow receipt to the existing task row and filter only exact matches |
| DTG-CLEAN-01A | Supervisor and status CLI remain responsibility-concentrated monoliths | high change collision and regression risk | extract behavior into existing canonical owners in bounded waves; retain one process and one CLI |
| DTG-CLEAN-01B | 815 historical brief names look like retired sidecar/unblock mechanisms | repository clutter, but not proven dead code | classify references and retention; prohibit blanket deletion |

DTG-CLEAN-01A and DTG-CLEAN-01B are one fourth cleanup stream because deletion safety must
be established before imports, fixtures, or evidence helpers are moved.

## 4. DTG-ARCH-01 — immutable V1 archive

### 4.1 Observed facts

The live anchor is
`task-state-events-v2.jsonl.legacy-anchor.json` and records:

| Field | Value |
|---|---|
| archive anchor SHA-256 | `702bd8a7c1a67e4525a2c289432f185647518d716dd7c4056bc624ebbdd536e3` |
| expected legacy journal SHA-256 | `185ba10f7d6d05a28d2b7bd53e8e13cb39a1297a25c249ddc7da5cfa063cc1bd` |
| expected byte size | `9,135,812,318` |
| expected events | `17,135` |
| original path | `/home/lupin/pantheon-ci-deploy/runtime/task-state-events.jsonl` |
| anchor creation | `2026-08-12T07:53:27Z` |

No matching filename was found under `/home/lupin` during this audit. The
existing verifier correctly returns `historical_archive_unavailable`. V2 hot
reads, writes, replay-from-head, and projection parity remain healthy.

### 4.2 What is already fixed

- The archive is not a runtime dependency.
- The immutable anchor is bound into V2 events.
- A relocated archive can be supplied explicitly.
- Size and SHA-256 are verified; a substitute cannot pass.
- Missing bytes have a distinct error classification.

### 4.3 What remains

This is data recovery/custody work, not another TaskStore implementation task.
The only successful technical closure is recovery of the exact 9.13 GB byte
stream. If all authorized backup locations are exhausted, the honest closure
is an explicit historical-loss disposition; the historical audit must continue
to report unavailable.

### 4.4 Forbidden repairs

- Do not regenerate a V1 journal from V2 or `ai-status.json`.
- Do not rewrite the anchor, size, hashes, counts, or original identity.
- Do not add V1 fallback or dual-store runtime mode.
- Do not make supervisor readiness depend on the archive.

## 5. DTG-CI-01 — tooling CI selection and coverage

### 5.1 Omitted suites

The tooling-only branch gate currently runs supervisor, runtime-state,
TaskStore, bridge, watchdog, status, config, and boundary tests. It does not
name these suites:

- `scripts/git/test_auto_integrator.py`
- `scripts/git/test_task_review_merge_gate.py`
- `scripts/git/test_github_review_bridge.py`
- `scripts/test_run_auto_integrator.py`
- `scripts/test_auto_integrator_install.py`
- `scripts/test_supervisor_launch_ownership.py`
- `scripts/test_supervisor_watchdog_install.py`

They passed locally as 263 tests and 7 subtests in 27.28 seconds.

### 5.2 Predicate defect

`component_boundary.py` exposes `product_touched` and `tooling_only`. The
workflow runs tooling tests only when `tooling_only == true` and product smoke
otherwise. Therefore a mixed change touching both `.orchestrator/` and
`services/` runs product smoke but skips the tooling suite.

The component manifest also lacks a general `scripts/git/` development-tooling
path. Some integrator changes are classified only as unknown paths. Unknown
currently means “not product” and therefore accidentally selects tooling-only;
that is not a stable ownership contract.

### 5.3 Required correction

Classification must expose independent booleans:

- `development_tooling_touched`
- `product_touched`
- `delivery_touched`

The workflow then runs every applicable gate. “Only” is useful for optimization
but must not be used to suppress a gate for mixed changes.

## 6. DTG-INT-01 — already-merged consumption

### 6.1 Root cause

`integration_candidates()` selects every `review_approved` row on every cron
run. When the PR is already merged, `integrate_candidate()` safely verifies the
merged PR and target ancestry, returns `already_merged`, and intentionally does
not mark the task done. The result is not persisted to canonical task truth.

For `OPS-SOURCE-FRONTIER-SCOPE-RECOVERY-20260829`, PR #5411 head
`254d2e7b05096dad3f6c7512db089ae2cbd8fe08` merged as
`8f8383b507b1fb631d44422031f01ebea5024d5e`. The task correctly remains
`review_approved` for its remaining Human/Ops/deployment closure, but the
integrator rediscovers and reproves the merge every five minutes.

### 6.2 Required correction

Persist a narrow `integration_receipt` on the existing canonical task row after
the runner verifies or performs a merge. Candidate discovery may skip only when
the receipt matches all of:

- task ID and current generation;
- repository and target branch;
- PR number;
- delivery-binding exact head;
- merge commit reachable from the target branch; and
- terminal integration result `merged` or `already_merged`.

Changing generation, delivery binding, PR, head, repository, or target branch
invalidates the receipt and makes the row eligible again. The receipt does not
mark the task done, approve it, deploy it, or satisfy product acceptance.

### 6.3 Why a local cache is rejected

A cron-local seen set or another runtime JSON file would be a second truth,
would be lost on reinstall/restart, and could suppress a changed task. The
receipt belongs to V2 canonical task state and must be written through one
governed status command.

## 7. DTG-CLEAN-01A — monolith responsibility concentration

### 7.1 Measured state

| File | Lines | Top-level symbols | Functions over 100 lines | Functions over 200 lines |
|---|---:|---:|---:|---:|
| `.orchestrator/supervisor.py` | 16,554 | 368 | 33 | 10 |
| `scripts/ai_status.py` | 11,545 | 271 | 22 | 7 |

Their principal test files add another 22,231 lines. No duplicate top-level
definition names were found. Runtime scanning found one supervisor process, one
singleton lock, one cron integration runner, and one V2 task authority.

The defect is therefore responsibility concentration, not two active
implementations.

### 7.2 Existing owners that must be extended

| Responsibility | Canonical owner |
|---|---|
| task transition and task persistence | `rewrite/task_machine.py`, `rewrite/task_state_store.py` |
| dispatch policy and admission | `dispatch_policy.py`, `rewrite/dispatch_admission.py` |
| provider health state | `rewrite/provider_health.py`, provider adapters |
| worker lifecycle/recovery facts | `rewrite/worker_lifecycle.py`, `rewrite/worker_recovery.py` |
| runtime cache/queue | `runtime_state.py` |
| development bridge | `development_bridge/` |
| task archive | `task_archive.py` |
| review and merge gate | `scripts/git/task_review_merge_gate.py`, canonical auto-integrator |

Extraction must move behavior to these owners instead of creating generic
manager/facade modules.

## 8. DTG-CLEAN-01B — historical evidence classification

`.orchestrator/task-briefs/` contains 2,227 tracked files. Of these, 805 names
contain `sidecar`, 39 begin with `integration_unblock_`, and the union is 815.

The name does not prove that the file is executable or dead:

- task briefs are consumed as review/delivery evidence;
- archived tasks and review records may retain their exact paths;
- `support/sidecars/` has a separate, existing retention tool;
- lock “sidecar” terminology is unrelated to the retired sidecar scheduler;
- `sidecar_cleanup.py` remains called by `evidence_retention_policy.py` for
  support evidence retention, not scheduling.

Therefore no blanket REMOVE action is justified. A file is removable only when
caller/reference search, canonical task/archive lookup, review binding, and
content custody all prove it is unnecessary. Otherwise its disposition is KEEP
historical evidence.

## 9. Priorities

1. DTG-CI-01 — smallest change and protects all later changes.
2. DTG-INT-01 — removes a live recurring loop without changing finalization.
3. DTG-CLEAN-01 — bounded extraction and evidence inventory; deletion only
   with proof.
4. DTG-ARCH-01 — can run independently because success depends on external
   custody, not code.
