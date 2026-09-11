# Supervisor runtime promotion drain

This is development tooling. `scripts/promote_supervisor_runtime.py` remains the
single promotion authority. The existing runtime admission lock, queue,
TaskStore recovery CAS, worktree guards, and capacity checks retain ownership.
A source merge does not activate the change on a running supervisor.

## Ordering and admission

The promotion entry point holds the existing integration lock and the stable
`.orchestrator/runtime-admission.lock`. Before stopping the incumbent it writes
and fsyncs `state.json.promotion` through `runtime_state_update`:

```text
(no fence) / ready / rolled_back
  -> draining: new epoch; incumbent and candidate root + source SHA pinned
  -> verifying: writers stopped; candidate config installed; candidate launched
  -> ready: exact candidate PID/runtime health and fresh canonical readback pass

failure -> stop any launched candidate -> verify storage/config restoration
        -> restart qualified incumbent -> verify incumbent health -> rolled_back
```

A failed stop before any worker drain restores incumbent admission. Incomplete
storage restoration, an unqualified candidate process, or failed rollback health
leaves admission closed and records the error; it does not claim recovery.
Changing the canonical coordination/admission root is rejected before stopping
anything. Storage paths within that same root retain the existing migration flow.

Queue admission reads the current fence under the shared authority lock. The
final adapter launch holds that lock across a fresh read and process creation,
so a detached runtime-phase snapshot cannot launch after cutover. The runner
also takes runtime admission **before** the canonical task lock, rechecks the
fence, then creates its provider child. Capacity, account health, task generation,
reviewer, authorization, and execution-resource checks still apply.

Both runtimes are denied during `draining` and `verifying`. After health passes,
only the accepted runtime may launch. A task with an unconsumed drain receipt
stays fenced until its existing recovery authority has accepted the continuation;
unrelated tasks can dispatch. The promotion lock is released while waiting for
health, allowing the candidate to boot and publish its existing health reports.
The migrated TaskStore lock is likewise released for canonical readback, and
reacquired before rollback storage changes.

## Drain receipt

`promotion` contains `schema_version`, random `epoch`, `phase`, `started_at`,
`incumbent`, `candidate`, `admitted_runtime`, `finished_at`, and `receipts` keyed
by worker run ID. Each receipt contains:

- `schema_version`, `epoch`, `reason=supervisor_runtime_promotion`, and
  `terminal_signal=15`;
- `worker`: task ID/generation, run ID, queue event ID, PID/start ticks,
  process-generation ID, lease acquisition time, and full command-runtime record;
- incumbent/candidate root and source SHA;
- `digest`: SHA-256 of the immutable receipt fields using sorted compact JSON;
- `status=prepared|drained|consumed` and preparation time;
- `terminal`: run ID, PID, runtime, signal, exit code, finish time, and the runner's
  matching `promotion_drain_digest`;
- on consumption, the canonical recovery receipt ID or explicit obsolete reason.

The digest binds facts; it is not a substitute for the existing filesystem,
process-generation, command-runtime, or TaskStore authority checks. The promoter
waits for the bound runner signal-readiness marker, then writes the prepared receipt before SIGTERM. A runner waiting for admission interrupts that wait and emits a terminal receipt without starting a provider. The runner echoes its digest only
for a matching current-process promotion intent on the SIGTERM terminal path.
Authorization revocation does not produce a planned drain. The promoter requires
confirmed process termination and a matching terminal marker (signal 15, exit
143 or -15) before committing `drained`. A missing/changed marker, SIGKILL,
ordinary process loss, stale epoch, changed task/lease binding, and wrong runtime
cannot create an accepted planned drain. Failed terminal validation rolls back
the receipt update; rollback discards unconfirmed intents.

## Continuation and recovery

Boot and polling suspend drain recovery until health acceptance. They intercept
verified dead promotion runs before generic missing-worker/failure classification.
The existing recovery store accepts the typed `worker_promotion_drained` envelope,
including the bound terminal evidence. Its existing task generation CAS fences
the old lease and its deterministic receipt ID deduplicates the continuation.
`worker_promotion_continuation_*` and `worker_promotion_drained` events distinguish
it from `worker_lost_lease`/`worker_process_missing`.

The original owner/reviewer pair is retained. If that lane has no capacity or is
unhealthy, the same receipt waits; it does not rotate to a fallback owner. Existing
workspace inspection, committed-progress preservation, and dirty/WIP protections
still decide whether the replacement can run. TaskStore owns replacement queue
materialization. Replaying after a crash between the TaskStore commit and runtime
CAS adopts that receipt and never creates a second one. A changed canonical task
generation consumes the obsolete runtime receipt without reviving the old task.
Invalid drain evidence follows ordinary recovery, not planned-drain labeling.

## First activation and operational evidence

An incumbent with active workers or runtime-phase reservations must already contain both fence-aware supervisor
launch and receipt-aware runner code. Otherwise promotion fails before stopping
it. For the first installation, let those workers and reservations finish naturally; do not invent
receipts for legacy runners or treat exit 143 as planned drain. No new pause,
queue, watchdog, or scheduler authority is introduced by this change.

The existing promotion JSON evidence adds `promotion_epoch`, `promotion_health`,
`rollback_health` when applicable, and the writer drain's run IDs. Inspect the
runtime `promotion` projection for unresolved epochs/receipts, and the canonical
TaskStore recovery records for pending/materialized continuation IDs. Do not
hand-edit those files to open admission.

After exact-head independent review, required checks, and supervisor integration
merge, the promotion operator must use the existing sealed command-runtime
promotion flow for that merged SHA. Collect terminal promotion output, candidate
health/canonical readback, and the existing watchdog health verification. Verify
that the accepted runtime SHA is served, each drained run has one continuation,
and no generic lost-lease event was emitted for a verified planned termination.
If first activation needs natural drain, or the live environment is unavailable,
record that separately: passing tests or a merged PR is not live activation proof.
