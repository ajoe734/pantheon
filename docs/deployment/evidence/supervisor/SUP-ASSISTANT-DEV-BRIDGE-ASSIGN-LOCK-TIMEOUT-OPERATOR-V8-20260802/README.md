# SUP-ASSISTANT-DEV-BRIDGE-ASSIGN-LOCK-TIMEOUT-OPERATOR-V8-20260802

Task-scoped evidence for removing the canonical assignment timeout from the
signed assistant dev bridge while preserving exact packet identity, durable
admission, authoritative journal readback, and at-most-once materialization.

| Field | Value |
|---|---|
| Owner | Codex |
| Reviewer | Human/Ops |
| Base | `dev` |
| Branch | `task/SUP-ASSISTANT-DEV-BRIDGE-ASSIGN-LOCK-TIMEOUT-OPERATOR-V8-20260802` |
| Implementation candidate | `a42b177be32c4b4e2fc8888179bf5eff014e6a83` |
| Composed `dev` base | `ef2b4f0a3988be6adbf56d24ec5df00df2717f39` |
| Review state | `review_pending` |

## Incident reproduction and lock owner

Two HMAC-SHA256 signed packets for
`L12-CURRENT-GAP-THREE-PASS-REAUDIT-20260801` failed on 2026-08-01:

| Packet | Drained | Packet digest | Result |
|---|---|---|---|
| `...T0355Z` | 03:56:12Z | `5c9e2426…dd45` | `ai_status.py assign timed out after 30s` |
| `...T0357Z-r1` | 03:57:39Z | `5fc8ebf6…b9a` | `ai_status.py assign timed out after 30s` |

Both receipts recorded `admissionStatus: not_attempted`,
`replayRejected: false`, and `retryable: false`. Direct canonical
assignment later succeeded and the task is now archived as done. Exact file
hashes and recovery readback are in
`incident-reproduction.json`.

The pre-fix call graph identifies the lock owner without guessing at a
competing process:

1. The supervisor process entered the exclusive runtime-admission lock.
2. While still owning it, the same process called the bridge inbox drain and
   owned `.drain.lock`.
3. The dispatcher then owned the packet replay lock while synchronously waiting
   for `ai_status.py assign`.
4. The child command required the governed runtime/task-state path held by its
   parent, so the parent waited for the child while the child waited for the
   parent. The only elapsed phase captured by the historical receipt was the
   30-second subprocess timeout; the receipt did not preserve the historical
   OS PID.

Scratch-root instrumentation now records the dispatch claimant PID, claim
creation/expiry, and per-phase `phaseTimingsMs`. The supervisor ordering
regression proves `bridge_drain -> runtime_lock_enter -> locked_cycle ->
runtime_lock_exit`; the full-cycle integration test proves a signed packet is
then admitted and read back from the authoritative event log.

## Delivered lock and recovery boundaries

- The supervisor drains the assistant inbox before exclusive runtime admission
  and carries only a scratch result snapshot into the locked cycle.
- Queue and replay locks protect short claim/commit transactions only. No
  governed subprocess, backoff sleep, or canonical readback runs while those
  locks are held.
- Exact packet claims bind packet ID, packet digest, every task-spec hash,
  claimant PID, timestamps, and expiry. A live exact duplicate is retryable;
  a different payload using the same ID fails closed.
- Packet payloads are bounded to 16 tasks. Per-packet dispatch and per-item
  processing OS fences make the live claimant authoritative even after a JSON
  claim expires; a crash releases the fence for deterministic recovery.
- Both OS fences open every managed parent directory through pinned directory
  descriptors with `O_DIRECTORY|O_NOFOLLOW`, and verify the leaf with `fstat`
  before locking. A symlinked claims parent or non-regular leaf fails closed
  without creating a file outside the repository/inbox boundary.
- The per-item processing fence remains held through dispatch, receipt fsync,
  archive/finalize, and retry/claim metadata cleanup. Expiring the advisory
  JSON claim while the first drainer is paused at receipt commit cannot start a
  second dispatch.
- Assignment/readback subprocess timeouts are bounded (2 seconds by default,
  capped at 5 seconds) and are retryable. Inbox retry metadata binds the signed
  packet digest and uses bounded exponential backoff.
- A timeout after one task resumes the exact packet. Existing bridge
  provenance in either active state or archive makes the first task an
  idempotent no-op; archived task content is not mutated or reused and each
  canonical task row exists exactly once.
- A processed receipt requires durable bridge admission, exact packet/task
  hashes, and canonical active-or-archive readback for every task ID.
  Receipt-only, replay-row-only, projection-only, and forged-retry states cannot
  establish success.
- Supervisor dispatch is fail-closed for bridge-owned tasks until an exact,
  durable packet admission record proves every task-spec hash was successfully
  materialized. A partial packet cannot queue or start a worker in the same
  `run_once` cycle.

Provider selection, configured quota groups, worker leases, owner/reviewer
identity, packet signature checks, artifact guards, and event-log integrity
remain unchanged.

## Concurrent authoritative benchmark

The 2,000-event scratch fixture is 137,953,724 bytes. Four workers executed
eight real governed commands (two each of approve, assign, note, and reopen)
while 18 full `supervisor.run_once` cycles were active.

| Shape | Legacy p95 | Current p95 |
|---|---:|---:|
| Uncontended journal command | 11.493s | 0.123s |
| Real governed commands during active supervisor cycles | 53.766s | **1.572s** |

All eight commands succeeded, six used worker leases, supervisor/command
execution overlapped across 18 full cycles, and exact projection parity held at
event 2,016. The
formal report has `meets_target: true` against the two-second p95 gate.

The benchmark reuses the task-state latency harness with the exact committed
bridge candidate `a42b177be32c4b4e2fc8888179bf5eff014e6a83`. It mutates only
scratch state.

## Human/Ops review remediation

Human/Ops rejected exact head
`2b956587f5eb9d35aebb95d191ab505afce6945f` after independently reproducing
two remaining fence failures. Candidate
`a42b177be32c4b4e2fc8888179bf5eff014e6a83` addresses both on top of
`origin/dev` commit `ef2b4f0a3988be6adbf56d24ec5df00df2717f39`:

1. dispatch and inbox fence parents are opened component-by-component without
   following symlinks, and two negatives prove no outside lock file is created;
2. the inbox processing fence now spans the complete item transaction, and a
   pause-after-dispatch/expired-claim negative proves exactly one dispatch with
   no concurrent receipt/finalize error; and
3. the complete supervisor and bridge matrix plus the 2,000-event benchmark
   were rerun from the new committed candidate instead of reusing the rejected
   head's report.

## Regression matrix

The focused suites cover:

- timeout before any task and timeout after one task;
- partial packet fail-closed supervisor dispatch and archived-prefix retry;
- crash/stale dispatcher claim and processing rename restart recovery;
- live dispatcher and inbox claimant fencing beyond expired JSON claims;
- dispatch/inbox claims-parent symlink escape rejection with no outside write;
- processing-fence ownership through receipt commit and metadata cleanup;
- packet task-count validation at the 16-task boundary;
- forged retry metadata and mismatched packet/spec identity;
- concurrent drainers and concurrent exact duplicate dispatch;
- missing/tampered admission, receipt-only recovery, and replay without
  authoritative materialization;
- a real signed packet through full supervisor `run_once`, governed assign,
  durable admission, authoritative event-log readback, and exact projection;
- unsigned packets, invalid signatures, artifact-scope guards, bridge actor
  identity, and worker-lease enforcement.

Owner validation:

```text
PYTHONPATH=.orchestrator pytest supervisor plus all bridge suites and CLIs
→ 600 passed, 147 subtests passed in 81.51s

2,000-event governed/full-cycle benchmark
→ p95 1.572s, 8/8 commands, 18 cycles, exact projection, meets_target true
```

The final candidate is also required to pass `py_compile`, JSON parsing,
commit trailer validation, and `git diff --check` before handoff.

## Scope, rollout, and rollback

Owned files are the supervisor bridge call boundary, bridge dispatcher/inbox,
their tests, and this evidence directory. There are no config, live runtime,
provider-policy, product-service, canonical JSON, dashboard bundle, or
deployment mutations.

Merge order is the scoped implementation commit followed by this evidence-only
commit, one PR to `dev`, exact-head Human/Ops review, merge, then governed
canonical closeout. Rollout is the merged supervisor/BFF source only. Rollback
is a revert of that merge commit; claims and retries are additive scratch
metadata and require no live-state rewrite.
