# Fixed-cadence and batch-mutation evidence

Task: `SUP-SCHEDULER-FIXED-CADENCE-BATCH-MUTATIONS-OPERATOR-V9-20260802`

Owner: Codex2
Reviewer: Human/Ops
Target: `dev`

## Result

The supervisor now starts cycles from an anchored monotonic deadline. Work consumes the configured interval; the loop no longer adds a full sleep after every cycle. An overrun advances in one arithmetic step to the first future deadline, reports the number skipped, and sleeps before the next start, so it cannot catch up by busy-looping.

The governed task packet's reported 51–80 second symptom is reproduced by 46 and 75 seconds of work followed by the former unconditional five-second sleep. The regression proves legacy deltas of 51 and 80 seconds. With deadline scheduling those cases start at 50 and 80 seconds; the nominal three-second case improves from eight seconds to the configured five.

Dispatch status transitions produced in one runtime cycle are now sent through one bounded payload and one governed `ai-status` process. That command reads one shared runtime snapshot and one authoritative task snapshot, validates every exact worker lease and owner/status CAS, then commits one state snapshot and one ordered activity outbox. A late CAS failure commits nothing. A simulated crash after canonical save recovers both audit rows exactly once.

## Lock boundary

The global order remains:

1. `runtime_admission`
2. `task_state`
3. `activity_audit`

Slow provider/auth work, assistant-bridge subprocesses, GitHub reads, merged-PR metadata, task-shadow reconciliation, remote git fetch, and the local continuation probe run before runtime admission. Activity append, canonical dispatch subprocess, archive subprocess, termination confirmation, and dashboard/evidence rendering run after it.

Worker marker/process/git observation, `process_queue` worktree preparation and adapter launch, and inactive/orphan/chair-review pruning now run in tokenized reservation phases. Each phase reserves in one short transaction, performs slow I/O against a detached snapshot with audit/status effects deferred, then commits in a second short whole-state CAS. If another runtime writer changes the state, that writer wins; the reserved snapshot is discarded and any process generation launched by the losing phase is terminated fail-closed.

Dispatch never performs a recovery network fetch under runtime admission. If a ref was not recorded by the pre-admission fetch, an already-resolving local ref is safe to reuse; an unresolved ref fails closed until a later pre-admission fetch succeeds.

## Bounded telemetry

Runtime telemetry stores aggregate scalars only: cycle elapsed, per-phase count/total/max, runtime lock hold, cadence overshoot/skips, queue-to-start count/average/max, and batch counts. Phase names and batch keys are source-owned and capped at 64 and 16 rows. No task body, event body, provider output, credentials, or unbounded timing history is retained.

The bounded sample is persisted only after reserved process/poll/prune phases, deferred activity/status/archive work, dashboard refresh, and runtime summary. Its elapsed time therefore covers the complete cycle. Scheduler completion telemetry is exception-isolated so a failed runtime-state read or write cannot terminate the deadline loop or stop later cycles.

## Governance

This change does not alter dependency or priority decisions, worker limits, failure-loop/chair triage, quota/auth fallback, active leases, explicit reviewer selection, or rollback behavior. Codex and Codex2 remain distinct configured account and quota groups. Human/Ops remains the required exact-head reviewer.

No live service was restarted or deployed. Live promotion belongs to the dependent governed V8 canary. Source rollback is a merge revert.

## Validation

- Supervisor: 561 tests passed.
- ai-status: 165 tests passed.
- `py_compile`: passed.
- JSON validation and `git diff --check`: passed.

The machine-readable acceptance and test mapping is in `evidence.json`. PR, exact reviewed head, review artifact, and merge commit are intentionally pending until the independent review stage.
