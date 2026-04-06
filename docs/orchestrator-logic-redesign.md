# Pantheon Orchestrator Logic Redesign

Last updated: 2026-04-06
Status: proposed operating model and cleanup plan

## 1. Problem Statement

The current orchestrator has the right major pieces, but the responsibility boundaries are still blurred:

1. `watch_events.py` can enqueue work based on task state changes.
2. `dispatch_ready_tasks()` in `supervisor.py` can also enqueue work for ready tasks.
3. `process_queue()` launches workers from the queue.
4. `poll_workers()` tries to recover, retry, fallback, or mark failures.

This creates three recurring failure modes:

1. **Duplicate dispatch**
   - the same task is queued by both watcher-driven events and ready-dispatch logic
   - result: duplicate `worker_started`, duplicate queue records, stale worker state

2. **Queue starvation after worker death**
   - when a worker dies, the task is not always returned cleanly to a re-dispatchable state
   - result: queue may be empty even though ready work still exists

3. **Provider hammering**
   - repeated failures on the same provider only lead to retry or inbox fallback
   - result: the system keeps leaning on the same broken provider instead of reassigning work

## 2. Design Goal

The orchestrator should have a single clean control flow:

1. canonical task truth lives in `ai-status.json`
2. queue is only a runtime execution buffer
3. ready-work discovery has one authoritative producer
4. worker failure always leads to one of:
   - retry
   - fallback
   - reassignment
   - terminal failure

## 3. Target Responsibility Split

### 3.1 Watcher

`watch_events.py` should only detect **state changes that are externally meaningful**.

Watcher responsibilities:

1. build a snapshot of task/handoff state
2. notice changes since the previous snapshot
3. update `state.json` bookkeeping (`last_scan_at`, seen snapshots, pending handoff keys)
4. optionally emit events for rare non-ready transitions such as:
   - explicit operator waiting states
   - external approvals
   - GitHub bus responses

Watcher should **not** be the normal producer for ready-task execution.

### 3.2 Dispatcher

The dispatcher is not a separate process. It is a scheduling phase inside `supervisor.py`.

Dispatcher responsibilities:

1. scan `ai-status.json`
2. determine which tasks are currently eligible to run
3. choose the correct target agent
4. enqueue queue events for those ready tasks

Dispatcher is the **only normal producer** for ready-task work.

### 3.3 Supervisor

The supervisor is the runtime coordinator.

Supervisor responsibilities:

1. run watcher bookkeeping
2. run dispatcher
3. consume queue events
4. launch workers
5. monitor workers
6. retry, fallback, or reassign failed work
7. reconcile queue records and runtime state

### 3.4 Auto Worker

An auto worker is just a launched provider session.

Worker responsibilities:

1. receive a wakeup payload
2. read canonical files
3. perform the assigned task/review
4. write back through the status script

Workers should never own scheduling policy.

## 4. Correct Queue Ownership Model

The queue should be treated as:

- a transient execution buffer
- not a source of truth
- not a work planner

### 4.1 What may enqueue into the queue

Allowed:

1. dispatcher for ready tasks
2. explicit non-ready external events that truly require immediate wake-up

Not allowed by default:

1. owner change -> auto enqueue
2. reviewer change -> auto enqueue
3. handoff creation -> auto enqueue
4. generic status change -> auto enqueue

Those should instead change task truth, and then dispatcher decides whether the task is actually runnable.

## 5. Ready-Task Scheduling Rules

Dispatcher should use this priority order:

1. `review`
2. owned `in_progress`
3. owned `todo` with satisfied dependencies
4. optionally helper-claim candidates later

Additional rules:

1. each agent has a configurable concurrency limit
2. if a task already has an active worker for that same agent, do not enqueue again
3. if a queue event already exists for that same task/agent/reason, do not enqueue again
4. if the task state changed since event creation, drop the old event

## 6. Worker Failure Lifecycle

This is the target failure ladder:

1. **Transient failure**
   - provider quota
   - timeout
   - temporary capacity issue
   - action: `retry_backoff`

2. **Retry exhausted**
   - action: either reassign or fallback

3. **Reassignment preferred**
   - for `review` tasks: change reviewer
   - for `todo` / `in_progress` tasks: change owner
   - write reassignment to `ai-status.json`

4. **Inbox fallback**
   - only when no safe alternate assignee exists

5. **Terminal failure**
   - task remains visible for manual follow-up

## 7. Reassignment Policy

Provider failure should not endlessly pin tasks to one LLM.

### 7.1 Review tasks

If the current reviewer repeatedly fails:

- keep owner
- reassign reviewer
- add a fresh pending handoff to the new reviewer

### 7.2 Owned work

If the current owner repeatedly fails:

- reassign owner
- keep or recompute reviewer
- update task `next` with the reassignment reason

### 7.3 Canonical write-back

All reassignment must be written back into:

1. `ai-status.json`
2. `ai-activity-log.jsonl`
3. regenerated `current-work.md`
4. docs-site mirrors

No reassignment should live only in runtime state.

## 8. Why Pending Events Happened Before

The old pending-event buildup came from a mix of:

1. duplicate event producers
   - watcher plus dispatcher both generating runnable work

2. durable queue without clean worker reconciliation
   - process died, queue record stayed

3. active-slot accounting using stale runtime state
   - dispatcher thought an agent was still busy

4. provider failures without reassignment
   - repeated retries on the same failing provider kept the same lane blocked

## 9. Proposed Simplified Runtime Loop

Recommended supervisor loop:

1. refresh supervisor heartbeat
2. watcher bookkeeping only
3. poll workers and reconcile dead/stale runs
4. prune stale queue events
5. dispatch ready tasks
6. process queue
7. poll workers again
8. sync GitHub bus
9. save state

Key constraint:

- only the dispatcher should create normal ready-task queue entries

## 10. Concrete Cleanup Plan

### Phase A: Responsibility Cleanup

1. disable watcher-driven ready-work enqueue
2. keep watcher only for snapshot/state bookkeeping
3. keep dispatcher as the sole ready-work producer

### Phase B: Queue and Worker Hygiene

1. ensure dead workers are reaped deterministically
2. if a worker dies before completion, move the task back to a redispatchable state
3. ensure queue records do not remain `started` when no active worker exists

### Phase C: Reassignment Stability

1. retry on transient failure
2. reassign after repeated failure
3. fallback only if no alternate agent is available

### Phase D: Concurrency Control

1. configurable concurrency per agent
2. stricter dedupe by `(task_id, target_agent, reason)`
3. ensure one review task does not endlessly block unrelated work when reassignment is possible

### Phase E: Observability

Dashboard should clearly show:

1. watcher health
2. supervisor health
3. queue depth
4. active workers
5. retry_backoff workers
6. reassignments
7. fallback/manual_pending workers

## 11. Recommended Near-Term Operating Policy

Until the runtime is fully stabilized:

1. use `dispatcher` as the only ready-task producer
2. keep `Gemini` out of primary auto-worker duty
3. let `Codex`, `Claude`, and `Grok` carry the currently unblocked work
4. treat `Gemini` as reserve/manual until provider stability improves

## 12. Success Criteria

We should consider the redesign successful when:

1. queue can become empty and be refilled automatically by dispatcher when ready tasks exist
2. no duplicate `worker_started` appears for the same task/agent/reason
3. dead workers are cleaned up without manual intervention
4. repeated provider failure causes reassignment instead of infinite hammering
5. dashboard reflects live runtime state clearly
