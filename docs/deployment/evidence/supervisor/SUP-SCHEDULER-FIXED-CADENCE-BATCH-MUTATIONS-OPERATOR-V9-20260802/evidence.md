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

Before an adapter may launch a process, the exact phase token now publishes a durable launch intent containing the task, queue event, provider/agent, attempt, request snapshot, wall epoch, and a Linux boot-relative tick boundary. A successful adapter result publishes the complete worker launch receipt before whole-state CAS. After supervisor death, a receipt is adopted directly; if death occurred after the external launch but before the receipt, recovery combines post-intent markers with exact live worker-runner identity from `ORCH_TASK_ID`, `ORCH_AGENT_ID`/`ORCH_PROVIDER`, `ORCH_RUN_ID`, PID, and Linux PID start ticks. Process start epoch is reconstructed from procfs `btime + start_ticks / CLK_TCK`; newly written intents also compare the exact boot-relative tick boundary. Marker recovery uses the runner's immutable `started_at`, never the heartbeat-refreshed file mtime. A pre-intent process or marker is therefore excluded before the unique-marker fallback can adopt it or bind it to a new queue event. One post-intent live generation is adopted. After a finite 30-second grace (hard-capped at 300 seconds), a conclusive zero-process scan clears an unchanged stale intent and retries the phase in the same entry; multiple live generations remain fail-closed.

The one-shot `claim_next_task_for_agent` entry point now uses the same reservation/CAS boundary. Provider capability cache reads, the failure-recovery activity snapshot, and planning-state input are prefetched before reservation. Queue/worktree/adapter work mutates the detached snapshot, and dashboard rendering runs only after the final CAS. The entry-point regression records `load_provider_report`, `process_queue`, and `refresh_dashboard_runtime_artifacts` at runtime-admission lock depth zero.

Dispatch never performs a recovery network fetch under runtime admission. If a ref was not recorded by the pre-admission fetch, an already-resolving local ref is safe to reuse; an unresolved ref fails closed until a later pre-admission fetch succeeds.

## Bounded telemetry

Runtime telemetry stores aggregate scalars only: cycle elapsed, per-phase count/total/max, runtime lock hold, cadence overshoot/skips, queue-to-start count/average/max, and batch counts. Runtime lock timing begins only after blocking acquisition succeeds, so contention wait and exclusive hold remain distinct evidence. Phase names and batch keys are source-owned and capped at 64 and 16 rows. No task body, event body, provider output, credentials, or unbounded timing history is retained.

The bounded sample is persisted only after reserved process/poll/prune phases, deferred activity/status/archive work, dashboard refresh, and runtime summary. Its elapsed time therefore covers the complete cycle. Scheduler completion telemetry is exception-isolated so a failed runtime-state read or write cannot terminate the deadline loop or stop later cycles.

## Rejected-head remediation

Human/Ops rejected PR #4520 heads `ab0d79e33bb1dfff452c93c32a96721168222ad9` and `fb923abe2182acb4b5c10a0e557040b968598008`. This repaired head addresses all four cumulative blocking findings:

1. Stale launch recovery no longer conflates zero and multiple marker candidates forever. No-launch and ambiguous dead-marker cases clear only after a conclusive exact-process scan, while two live exact generations preserve the reservation fail-closed. The pre-existing forked hard-crash and durable-receipt tests continue to prove adoption before redispatch.
2. Self-claim no longer wraps provider report loading, `process_queue` worktree/adapter launch, and dashboard rendering in the outer exclusive runtime lock. Its public entry-point regression proves those three slow calls execute at lock depth zero around short reservation/CAS transactions.
3. A failed `_run_with_deferred_dispatch_status_syncs` operation now discards deferred canonical dispatch, archive, and activity effects. Only exact PID-start-bound termination cleanup runs before the original error is re-raised.
4. Launch recovery now proves that the candidate generation follows the durable intent. A fake procfs regression rejects an exact task/agent/run wrapper whose reconstructed start epoch predates `prepared_epoch_seconds`; another regression proves Codex and Codex2 remain distinct candidates. A fresh-mtime marker whose immutable `started_at` is from 2020 is rejected both at marker selection and through the unique-marker recovery path, while the post-intent process and forked crash-adoption cases remain green.

## Governance

This change does not alter dependency or priority decisions, worker limits, failure-loop/chair triage, quota/auth fallback, active leases, explicit reviewer selection, or rollback behavior. Codex and Codex2 remain distinct configured account and quota groups. Human/Ops remains the required exact-head reviewer.

No live service was restarted or deployed. Live promotion belongs to the dependent governed V8 canary. Source rollback is a merge revert.

## Validation

- Supervisor: 573 tests and 158 subtests passed.
- ai-status: 165 tests and 31 subtests passed.
- `py_compile`: passed.
- JSON validation and `git diff --check`: passed.

The machine-readable acceptance and test mapping is in `evidence.json`. PR, exact reviewed head, review artifact, and merge commit are intentionally pending until the independent review stage.
