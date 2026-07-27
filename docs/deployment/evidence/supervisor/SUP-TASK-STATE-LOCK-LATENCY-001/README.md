# SUP-TASK-STATE-LOCK-LATENCY-001 — evidence

Bound supervisor task-state lock latency and projection truth.

| | |
|---|---|
| Owner | Claude |
| Reviewer | Codex2 |
| Phase | Supervisor Runtime Repair |

## What was actually wrong

The multi-minute stalls were not lock bypass, config drift, or a stuck worker.
They were **replay cost paid under the canonical lock**.

Every read of the authoritative journal replayed and revalidated the whole log,
and `validate_event` canonically re-serializes each event's full state twice
(once for `state_sha256`, once for the event digest, which embeds the state).
The callers then stacked those passes:

| Caller | Full journal replays, before |
|---|---|
| `ai_status.load_state` | 2 (`load_events` + `project_latest_state`) |
| `task_state_store.append_state_commit` | 2 (head lookup + full-replay readback) |
| `common.load_status` (many times per cycle) | 2 |
| `supervisor.sync_task_state_shadow` | 4 (`load_events`, `project_latest_state`, then `verify_projection` doing both again) |

So one governed status command replayed a ~157MB / 2050-event journal four
times, and the supervisor's reconciliation phase did the same four times per
cycle **while holding the exclusive canonical task-state lock**. That is the
771s tick heartbeat (22:35:04Z → 22:47:55Z) and the ~517s hold that followed,
with reviewer and status processes queued on runtime-admission inode 807896.

Two correctness defects sat on the same surface:

* `caught_up` was assigned the *divergence* predicate — exactly inverted. A
  healthy cycle published `caught_up: false`; a cycle that had just rewritten a
  drifted board published `caught_up: true`.
* `verify_projection(path, expected)` re-read the journal in its own lock
  window. Paired with a board read taken earlier, a report could straddle two
  journal generations — the observed transient `event_count=2046` with the
  expected SHA from event 2045 and the projected SHA from event 2046, which a
  stable rerun at event 2049 then contradicted with `ok=true`.

## What changed

`.orchestrator/rewrite/task_state_store.py`

* `load_snapshot(path)` — one lock window, one validated pass, returning the
  event count, head event id, projected state, and its digest as a single
  consistent record.
* **Validated-prefix checkpoint** (`<journal>.checkpoint.json`). Validation is a
  pure function of the journal bytes, so a verdict for a byte range stays valid
  while that range is unchanged. The checkpoint is honoured only when a SHA-256
  over the exact prefix it claims still matches on disk, and its recorded head
  event is self-validated. Every byte is still hashed on every read; only the
  per-event digest work for already-validated events is skipped. Any mismatch,
  truncation, corruption, or `PANTHEON_TASK_STATE_STORE_FULL_REPLAY=1` degrades
  to a full replay.
* The journal is `mmap`-ed rather than copied into the heap; hashing 160MB
  costs less than reading it into a `bytes` object did.
* `verify_snapshot(snapshot, expected)` — pure and lock-free, so a report cannot
  span two generations. `verify_projection` is now a thin wrapper over it.
* `append_state_commit` reads back exactly the bytes it wrote at their offset
  and checks the resulting file size, instead of replaying the whole journal to
  inspect its last line.

`.orchestrator/supervisor.py`

* `sync_task_state_shadow` reads the journal **once**, reports `caught_up` as
  parity-after-reconciliation, and adds a separate `repaired` flag for whether a
  write was needed. The phase's return value (the cycle's "changed" signal) is
  now `repaired`.
* `probe_provider_reports` runs the provider capability probe — which shells out
  to `gh` for auth and version checks — **before** the cycle takes the exclusive
  runtime-admission lock.
* `record_runtime_lock_hold` publishes `runtime_lock_hold_seconds`,
  `runtime_lock_hold_peak_seconds`, and `runtime_lock_hold_exceeded` on every
  cycle, with a console warning past
  `supervisor.runtime_lock_hold_warn_after_seconds` (default 30s). The live 771s
  hold left no trace in runtime state; an equivalent regression now does.

`scripts/verify_task_state_store.py`

* Samples the board and the journal inside **one** canonical task-state lock
  domain, closing the two-window race. `--full-replay` forces the deep audit.

`scripts/ai_status.py`, `.orchestrator/common.py`

* Both authoritative read paths use `load_snapshot` — one validated pass instead
  of two.

## Measurements

`bench-report.json`, produced by `task_state_lock_latency_bench.py` on a
2050-event / 159.5MB fixture (live: 2050 events / ~157MB):

| Shape | Legacy p95 | Current p95 |
|---|---|---|
| Read board + commit, uncontended | 20.9s | **0.261s** |
| Read board + commit, 4 concurrent commands during an active supervisor cycle | 71.3s | **1.153s** |

The legacy contended figure (66.6s p50 / 71.3s p95) reproduces the live
observation that each note command took roughly 55-90s. The current contended
p95 of 1.153s is under the 2s target, with no lock bypass, no config edit, and
no worker termination. A cold first read with no checkpoint present costs 3.53s
once, after which reads are checkpoint-accelerated.

Reproduce:

```bash
PYTHONPATH=.orchestrator python3 \
  docs/deployment/evidence/supervisor/SUP-TASK-STATE-LOCK-LATENCY-001/task_state_lock_latency_bench.py \
  --events 2050 --task-rows 34 --samples 8 \
  --contention-workers 4 --contention-commands 2 --contention-seconds 45
```

Exit status is 0 only when both the uncontended and contended p95 are under 2s.
The harness builds its fixture in a scratch directory and never touches
canonical state.

## Verification

```
PYTHONPATH=.orchestrator python3 -m pytest \
  .orchestrator/test_supervisor.py .orchestrator/test_runtime_state.py \
  .orchestrator/rewrite/ scripts/test_ai_status.py \
  scripts/test_verify_task_state_store.py scripts/test_status_file_guard.py \
  scripts/test_dispatch_twelve_loop_gap_2026_07_26.py -q
→ 791 passed, 76 subtests passed
```

New regressions:

* `test_task_state_store.py` — checkpoint reuse and tail-only parsing;
  checkpointed result identical to a forced full replay; edited history rejected
  despite a checkpoint; truncation not served from a stale checkpoint;
  unusable checkpoint degrades and self-repairs; append does not replay the
  journal for its own readback; read-then-commit costs one pass; short write
  detected; `verify_snapshot` reports one generation.
* `test_supervisor.py` — `caught_up`/`repaired` separated in both authoritative
  and shadow mode; reconciliation replays the journal once per cycle; the report
  describes one generation even when a commit lands mid-phase; provider probes
  run before the runtime lock is taken; lock-hold budget published and flagged.

## Scope not covered by this task

Acceptance item 1 names three classes of unbounded wait under the exclusive
runtime-admission lock. Provider probes are now outside it, and the journal
replay that made every hold multi-minute is gone. Two residual waits remain
inside the lock and are **not** addressed here, because both need a subsystem
split rather than a lock change:

* `sync_github_bus` runs `gh` network subprocesses inside the cycle. Removing it
  from the hold requires splitting `github_bus.sync_github_bus` into a network
  fetch and a state apply, so only the apply runs under the lock.
* `terminate_worker_pid` → `rewrite_worker_lifecycle.confirm_kill` polls with
  sleeps (bounded to a few seconds) from `poll_workers` and `process_queue`.

Both are now visible rather than silent: any cycle whose hold exceeds the
configured budget publishes `runtime_lock_hold_exceeded` and logs the hold
duration. Recommend a follow-up task for the `github_bus` fetch/apply split.
