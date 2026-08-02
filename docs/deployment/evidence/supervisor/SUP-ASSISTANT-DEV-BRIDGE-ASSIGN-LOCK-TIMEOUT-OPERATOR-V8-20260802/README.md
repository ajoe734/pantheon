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
| Implementation candidate | `64bf42c4ee316c8968feca2006bd9e1e3c84ea7f` |
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
- Assignment/readback subprocess timeouts are bounded (2 seconds by default,
  capped at 5 seconds) and are retryable. Inbox retry metadata binds the signed
  packet digest and uses bounded exponential backoff.
- A timeout after one task resumes the exact packet. Existing bridge
  provenance makes the first task an idempotent no-op; each canonical task row
  exists exactly once.
- A processed receipt requires durable bridge admission, exact packet/task
  hashes, and canonical active-or-archive readback for every task ID.
  Receipt-only, replay-row-only, projection-only, and forged-retry states cannot
  establish success.

Provider selection, configured quota groups, worker leases, owner/reviewer
identity, packet signature checks, artifact guards, and event-log integrity
remain unchanged.

## Concurrent authoritative benchmark

The 2,000-event scratch fixture is 137,953,724 bytes. Four workers executed
eight real governed commands (two each of approve, assign, note, and reopen)
while 17 full `supervisor.run_once` cycles were active.

| Shape | Legacy p95 | Current p95 |
|---|---:|---:|
| Uncontended journal command | 11.398s | 0.117s |
| Real governed commands during active supervisor cycles | 52.579s | **1.393s** |

All eight commands succeeded, six used worker leases, supervisor/command
execution overlapped, and exact projection parity held at event 2,016. The
formal report has `meets_target: true` against the two-second p95 gate.

The benchmark reuses the task-state latency harness with the exact committed
bridge candidate `64bf42c4ee316c8968feca2006bd9e1e3c84ea7f`. It mutates only
scratch state.

## Regression matrix

The focused suites cover:

- timeout before any task and timeout after one task;
- crash/stale dispatcher claim and processing rename restart recovery;
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
pytest .orchestrator/test_supervisor.py plus bridge dispatcher/inbox/reliability
→ 584 passed, 147 subtests passed

pytest test_dev_bridge_inbox_cli.py test_dev_bridge_dispatch_cli.py
→ 8 passed

2,000-event governed/full-cycle benchmark
→ p95 1.393s, 8/8 commands, exact projection, meets_target true
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
