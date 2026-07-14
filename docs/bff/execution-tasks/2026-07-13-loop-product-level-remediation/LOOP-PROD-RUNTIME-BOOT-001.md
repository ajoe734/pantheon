# LOOP-PROD-RUNTIME-BOOT-001 — Shared runtime/task/audit lock protocol bootstrap

Status: pre-dispatch external prerequisite

Program:
`loop-product-level-remediation-2026-07-13`

Canonical contract SHA-256: `04f382e320292e11df3b4668ec4383819b9c9abadcc48f3b9150a7abcb65141e`

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
- `.orchestrator/runtime-task-audit-lock-capability.json`
- `.orchestrator/runtime-task-audit-writer-registry.json`
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
- the runtime guard parses the canonical runtime, event-queue, and approval-queue sources with exact schemas and returns exactly one decision containing the protocol version, lock mode, ordered task IDs, ordered source digests, aggregate snapshot digest, exact conflict records, and stable reason ID; the dispatcher separately parses task and audit schemas under the next two ordered locks before acting, and byte-substring scans or default-empty fallbacks are forbidden
- missing, empty, malformed, unreadable, wrong-version, or default-substituted runtime state, event queue, approval queue, or task state fails closed; a present activity source is parsed strictly, while a genuinely pre-dispatch empty append-only audit is valid only when exact outbox preflight also finds no conflicting event
- queued, running, admitted, approval-suspended, duplicate, empty, or foreign task IDs fail closed with exact reason IDs and no canonical write
- the capability manifest and exhaustive writer registry bind every registered writer blob, the executing dispatcher bytes, a signed bootstrap completion verdict with exact verifier capability, key, policy, revocation check, and protected ledger identity, a protected `verify_runtime_lock_capability` decision, and `dev` ancestry to the same exact merged commit; a locally fabricated, format-valid, or self-reported manifest is rejected
- each registered helper holds its stable sidecar across the complete load, validation, mutation, fsync, replace/append/rotation, and readback transaction; repository tests reject unregistered direct canonical writes
- post-`os.replace` contenders remain serialized by stable sidecars; deterministic process-level tests reproduce the old inode race and prove the second transaction cannot cross it
- crash, kill, restart, concurrent enqueue, concurrent status write, log rotation, and outbox recovery tests preserve the newest task/runtime/audit truth without lost or duplicated events
- the exact merged bootstrap SHA passes the planning dispatcher's live strict dry-run against canonical state with zero writes before the 48-task materialization is authorized

## Required proof

- exact canonical task, admitted run/provider/slot/worktree/scope/branch, PR, checks, merge SHA, and distinct-runtime exact-head review
- lock-order trace and process-level contention evidence for all three stable lock files
- strict malformed/missing/runtime-busy matrix and no-write hashes
- exact decision-schema/source-digest/conflict/reason-ID mutation matrix
- merged-commit ancestry, writer-blob, executing-dispatcher, capability-manifest, exhaustive writer-registry, signed verdict/key/policy/revocation/ledger, and protected verifier-decision evidence
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
