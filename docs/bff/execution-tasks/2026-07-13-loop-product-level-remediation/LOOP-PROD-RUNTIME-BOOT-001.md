# LOOP-PROD-RUNTIME-BOOT-001 — Shared runtime/task/audit lock protocol bootstrap

Status: pre-dispatch external prerequisite

Program:
`loop-product-level-remediation-2026-07-13`

Canonical contract SHA-256: `ba34ce0a5ed90ac21c63d8d89e345550cae565a33e4a9d083883daa36e2e48fd`

Plan:
`docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/REMEDIATION_GAP_ADDENDUM_2026-07-13.md`

## Ownership

| Field | Value |
| --- | --- |
| Owner | Codex2 |
| Reviewer | Codex |
| Repository | `pantheon` |
| Merge target | `dev` |
| Task class | fleet product implementation; external prerequisite to the 48-task primary catalog (49 execution tasks total) |

The planning controller may author this contract, create its canonical task,
monitor it, and accept or reject evidence. It must not implement the declared
product artifacts. Pantheon PR `#3554` is an untrusted implementation input;
the admitted fleet may audit, split, rewrite, or discard it.

## Purpose

The 48-primary-task dispatcher cannot safely mutate canonical task state until every
canonical task writer and every runtime admission producer shares stable lock
inodes. This task installs that prerequisite before any live dispatcher apply
or live dry-run is accepted. It is deliberately outside the catalog mutation
transaction so the unsafe transaction is never used to bootstrap its own lock.

## Declared artifacts

- `.orchestrator/runtime_state.py`
- `.orchestrator/supervisor.py`
- `.orchestrator/approval_queue.py`
- `.orchestrator/adapters/file_inbox.py`
- `.orchestrator/watch_events.py`
- `.orchestrator/supervisor_watchdog.py`
- `scripts/ai_status.py`
- `.orchestrator/test_runtime_state.py`
- `.orchestrator/test_supervisor.py`
- `scripts/test_ai_status.py`
- `docs/deployment/fleet-runtime-lock-protocol.md`
- `docs/deployment/evidence/loop-product-level/LOOP-PROD-RUNTIME-BOOT-001`

## Acceptance

- the repository exports exact protocol version `1` for runtime admission, canonical task state, and activity audit locking; the dispatcher refuses missing, older, newer, or partial protocol implementations
- the global acquisition order is stable runtime admission lock, then stable canonical task-state lock, then stable activity-audit lock; no code path acquires them in reverse order
- runtime queue/event producers, worker admission/finalization, approval suspend/resume, watchdog recovery, and supervisor whole-file RMW use the same never-replaced runtime lock inode
- every `ai-status.json` writer, including `scripts/ai_status.py`, supervisor reassignment/finalization, archive transitions, and the loop dispatcher, uses the same never-replaced `.orchestrator/task-state.lock` inode
- every append, scan, rotation, prune, recovery, and replay of `ai-activity-log.jsonl` and its rotated archives uses the same never-replaced `.orchestrator/activity-audit.lock` inode
- one strict multi-task guard holds runtime serialization across queue, worker, execution-admission, and pending-approval inspection and across the nested task-state transaction
- missing, empty, malformed, unreadable, wrong-version, or default-substituted runtime state, event queue, approval queue, task state, or audit source fails closed
- queued, running, admitted, approval-suspended, duplicate, empty, or foreign task IDs fail closed with exact reason IDs and no canonical write
- post-`os.replace` contenders remain serialized by stable sidecars; deterministic process-level tests reproduce the old inode race and prove the second transaction cannot cross it
- crash, kill, restart, concurrent enqueue, concurrent status write, log rotation, and outbox recovery tests preserve the newest task/runtime/audit truth without lost or duplicated events
- the exact merged bootstrap SHA passes the planning dispatcher's live strict dry-run against canonical state with zero writes before the 48-task materialization is authorized

## Required proof

- exact canonical task, admitted run/provider/slot/worktree/scope/branch, PR, checks, merge SHA, and distinct-runtime exact-head review
- lock-order trace and process-level contention evidence for all three stable lock files
- strict malformed/missing/runtime-busy matrix and no-write hashes
- concurrent enqueue/status/outbox/rotation crash matrix
- canonical live dry-run before/after hashes bound to the merged bootstrap SHA
- redacted checksummed evidence manifest and residual-risk verdict

## Bootstrap ceremony

1. Merge this planning packet first; do not run its dispatcher.
2. Under a documented supervisor maintenance window, prove no live status writer
   or runtime event producer is active, create this one canonical task, and
   restart the supervisor so a fleet worker is admitted normally.
3. The fleet implements and merges this task through a reviewed PR.
4. Restart and read back the exact merged supervisor/runtime identity.
5. Only then may the dispatcher import protocol version `1`, acquire the three
   locks in order, and perform a strict live dry-run or apply.

Failure at any step leaves the 48-primary-task catalog unmaterialized and the program
active. A local branch, draft PR, test-only shim, process scan without the
locks, or planner-authored product patch cannot satisfy this prerequisite.
