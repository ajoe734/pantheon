# SUP-TASK-STATE-LOCK-LATENCY-001 — evidence

Task-scoped evidence for bounding supervisor task-state/runtime-admission
latency while preserving journal, lease, and process-identity safety.

| Field | Value |
|---|---|
| Owner | Codex |
| Reviewer | Codex2 |
| Branch | `task/SUP-TASK-STATE-LOCK-LATENCY-001` |
| Candidate | `2abc735b024917d0cd1e03784ca1e27040540341` |
| Implementation PR | [#4263](https://github.com/ajoe734/pantheon/pull/4263) |
| Merge commit | `52aa8a623e68336e1965d7241950cb3c22f0c827` |
| Review state | Pending fresh independent review |

## Incident and root cause

The live incident involved supervisor PID 901543, a 771-second tick
(22:35:04Z–22:47:55Z), a subsequent roughly 517-second exclusive hold, and a
reviewer reopen that waited about nine minutes on runtime-admission inode
807896. Human/Ops note commands over the roughly 157 MB / 2050-event journal
took about 55–90 seconds.

The original journal path repeatedly replayed and revalidated the entire file:
status reads, append head lookup/readback, the many `common.load_status` calls in
one supervisor cycle, and projection verification all duplicated the same
work. Network and process waits then compounded the problem because some were
still reachable from `_run_once_locked`.

The failed Codex2 review after merged PRs #4239, #4250, and #4253 identified
three remaining defects:

- `auto_commit_archive.py` and squash-merge PR lookup could still run network
  subprocesses under runtime admission;
- deferred termination could act without a process start-tick identity and
  report a worker terminal before post-lock confirmation;
- the previous contention evidence was synthetic and did not invoke real
  governed commands or the full supervisor cycle.

## Delivered boundaries

### Runtime and network work

- Provider probes, GitHub bus sync, exact worker-base fetches, ownerless
  squash-merge PR lookup, and task-state projection reconciliation occur before
  exclusive runtime admission.
- Ownerless PR metadata is bound to the exact task, owner, reviewer, worker run,
  dispatch time, delivery head, branch, and status generation. The locked
  consumer rejects stale or missing snapshots and cannot fall back to `gh`.
- Auto-archive is tokenized under the lock, executed afterward, then applied
  under a new short lock only if the pending token and timestamp are still
  current.
- Derived `current-work`/dashboard/docs-site rendering occurs after canonical
  task and runtime locks. A separate derived-view lock plus current-projection
  digest prevents an older command from overwriting newer views.

### Journal and projection truth

- `load_snapshot` returns event count, last event identity, projected state, and
  state SHA from one stable journal generation.
- The validated-prefix checkpoint still binds its cached head to the actual
  journal prefix. A process-local cache reuses a fully validated snapshot only
  while device, inode, size, mtime-ns, and ctime-ns identify the same journal
  generation; append, rewrite, truncate, or replacement forces validation.
  `PANTHEON_TASK_STATE_STORE_FULL_REPLAY=1` bypasses acceleration.
- A governed authoritative mutation holds one journal writer lock and advances
  one rolling snapshot across its outbox saves. Sequence, previous-event SHA,
  file/directory fsync, exact append readback, and nonterminal-drop validation
  remain enforced.
- `caught_up` now means parity after reconciliation. `repaired` separately
  records whether the cycle wrote a repair.

### Worker termination

- A live PID without `worker_pid_start_ticks` fails closed and receives no
  signal.
- The locked phase only schedules `(pid, start_ticks)`. `SIGTERM`, grace
  polling, and escalation happen after runtime admission is released and stop
  if the numeric PID has been reused.
- A worker stays nonterminal until a later cycle observes that post-lock
  confirmation actually removed the process.

## End-to-end benchmark

`task_state_lock_latency_bench.py` builds an isolated coordination root and a
2050-event, 141,402,624-byte (134.852 MiB) journal. The current path launches:

- four concurrent worker processes;
- eight real `scripts/ai-status.sh` mutations: two each of `approve`, `assign`,
  `note`, and `reopen`;
- six commands with exact worker/runtime/worktree leases and two Human/Ops
  assigns;
- a separate process continuously executing the full `supervisor.run_once`.

The harness refuses dirty candidate executable paths and records the exact
candidate SHA. It verifies every command exit, supervisor overlap, final event
count, and exact journal/projection SHA parity.

| Shape | Legacy p95 | Current p95 |
|---|---:|---:|
| Snapshot/commit microbenchmark, uncontended | 12.884s | 0.115s |
| Real governed commands during full supervisor cycles | 59.327s | **1.296s** |

The current run completed 8/8 commands, overlapped 17 full `run_once` calls,
finished at event 2066, and reported `exact_projection: true`. All current
command latencies were at or below 1.296 seconds. The formal report has
`meets_target: true`.

Reproduce from a clean committed candidate:

```bash
PYTHONPATH=.orchestrator .venv-pantheon/bin/python3 \
  docs/deployment/evidence/supervisor/SUP-TASK-STATE-LOCK-LATENCY-001/task_state_lock_latency_bench.py \
  --events 2050 --task-rows 30 --samples 8 \
  --contention-workers 4 --contention-commands 2 \
  --contention-seconds 45 \
  --json docs/deployment/evidence/supervisor/SUP-TASK-STATE-LOCK-LATENCY-001/bench-report.json
```

## Validation

```text
env -u PANTHEON_STATUS_ROOT \
    -u PANTHEON_TASK_STATE_STORE_MODE \
    -u PANTHEON_TASK_STATE_EVENT_LOG \
    PYTHONPATH=.orchestrator \
    .venv-pantheon/bin/python3 -m pytest -q \
      .orchestrator/test_supervisor.py \
      .orchestrator/test_runtime_state.py \
      .orchestrator/rewrite/test_task_state_store.py \
      .orchestrator/rewrite/test_worker_lifecycle.py \
      scripts/test_ai_status.py
→ 685 passed, 82 subtests passed in 144.67s
```

Key regressions cover:

- post-lock auto-archive execution and stale-token rejection;
- prelock ownerless PR lookup, no locked network fallback, and stale identity
  rejection;
- missing start ticks, PID reuse, post-lock confirmation, and nonterminal
  worker state;
- stable snapshot cache invalidation, forced full replay parity, history
  tamper/truncation/corrupt checkpoint rejection, short append detection, and
  multi-save monotonic hash chaining;
- task-state reconciliation before runtime admission and correct
  `caught_up`/`repaired` semantics;
- stale derived-view suppression.

## Non-interference and delivery state

- No `.orchestrator/config.json` or live deployment configuration was changed.
- No live worker was signalled or killed.
- Canonical status updates used the governed installed command with
  `AI_NAME=Codex`; no status, activity-log, or current-work file was hand
  edited.
- The benchmark uses scratch state only.
- Generated dashboard test artifacts and two empty test lock sidecars were
  removed before staging.

Implementation PR #4263 merged to `dev` after Commit trailers, Runtime mirror
guard, Python packaging provision, and Smoke acceptance passed. Benchmark and
owner validation are complete. Fresh Codex2 independent review and owner
closeout are still required; this evidence does not claim review approval.
