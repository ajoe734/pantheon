# Supervisor worker-wrapper identity

This is development tooling. It corrects how the supervisor recognizes a live
`worker_runner.py` wrapper process for capacity scanning and launch-recovery
identification. It does not raise any capacity limit, add a second scheduler,
weaken task review, change credentials, or alter active leases.

## The bug

Two independent live audits found the scheduler counted worker wrappers with a
raw substring search over the whole `/proc/<pid>/cmdline` blob:
`"worker_runner.py" in cmdline`. Each real worker run also spawns provider
descendants (the CLI shim, the CLI binary, and any `bwrap` sandbox child) that
inherit the same wake prompt as an argv value. When that inherited prompt text
itself happens to contain the literal string `worker_runner.py` — for example
while quoting the task's own artifact path in its instructions — the substring
search matches the descendant too. The scheduler therefore reported six active
processes for three real wrappers, so `max_concurrent_workers` believed the
fleet was at capacity when three-quarters of the real wrappers were counted a
second or third time. The watchdog (`supervisor_watchdog.cmdline_is_worker_runner`)
already used an exact argv-path predicate and correctly reported three.

A substring in arbitrary argv text is not an identity boundary: it can also
occur in a completely unrelated free-text argument, is not anchored to a path,
and gives no way to tell the wrapper apart from a descendant that merely
repeats its parent's argv.

## The fix

Both call sites that need worker-wrapper identity now share the watchdog's
`cmdline_is_worker_runner(parts)` predicate instead of each keeping — and
subtly diverging from — their own substring check:

- `supervisor.scan_live_worker_pids_by_agent` (fleet capacity / live-worker
  count reconciliation)
- `supervisor._proc_worker_runner_launch_marker` (launch-recovery identity
  evidence used to recover an intent's exact live PID/start-tick generation)

`cmdline_is_worker_runner` NUL-splits `/proc/<pid>/cmdline` into its real argv
tokens (never the space-joined display string) and requires that one of the
first four tokens:

- is path-shaped (starts with `/` or `.`) and contains no whitespace, and
- has `Path(token).name == "worker_runner.py"`, and
- has `.orchestrator` among `Path(token).parts`.

A wake-prompt argument that merely contains the same text fails this
predicate: it is not path-shaped (it contains spaces) and it is not one of the
process's own path tokens. A `bwrap` sandbox child or a provider CLI process
fails it for the same reason — their own argv[0..3] path tokens name the
sandbox or CLI binary, not `worker_runner.py` under `.orchestrator`.

Both capacity scanning and launch-recovery identification now use this one
predicate, so a real wrapper is counted/identified exactly once and a
descendant that carries the same wake prompt is excluded from both.

## What did not change

- Zombie/dead-process filtering in `scan_live_worker_pids_by_agent` (the `Z`/`X`
  state check and the `pid_is_alive` gate) is unchanged and still runs before
  the identity predicate.
- `_proc_worker_runner_launch_marker`'s task/agent environment binding
  (`ORCH_TASK_ID`, `ORCH_AGENT_ID`, `ORCH_PROVIDER`), PID start-tick generation
  check, and fail-closed `RuntimeError` behavior when start-tick or boot-epoch
  evidence is unreadable are unchanged; only the initial cmdline gate at the
  top of the function was tightened.
- Run-id handling, lease behavior, promotion-drain behavior, and the existing
  `max_concurrent_workers` capacity ceiling are unchanged. This corrects
  identity, not the limit.

## Runtime health / count observation (read-only)

Runtime activation is a separate governed promotion after source merge
(`scripts/promote_supervisor_runtime.py`); this task changes only the source
tree. As a read-only observation ahead of that promotion: the currently
running supervisor is on `dispatch_runtime.command_root`/`source_sha`
`ba6c9e99ec4a0b09ca85b30ab18bb862a3e42e58` (see `evidence.json`), which
predates this fix and therefore still runs the substring-based scan in its
live process image until an operator runs the promotion script against the
merged commit.

## Verification

Focused procfs regressions (`.orchestrator/test_supervisor.py`):

- `test_scan_live_worker_pids_excludes_prompt_text_and_bwrap_descendants` —
  a real `.orchestrator/worker_runner.py` wrapper is counted once; a `bwrap`
  descendant and a provider CLI descendant that both carry the same wake
  prompt (one of them quoting the literal text `worker_runner.py`) are
  excluded.
- `test_proc_worker_runner_launch_marker_rejects_prompt_text_match` — a
  descendant whose prompt argument contains the literal text
  `worker_runner.py`, with matching `ORCH_TASK_ID`/`ORCH_AGENT_ID` in its
  environment, is still rejected as launch-recovery evidence because its argv
  is not path-shaped.
- `test_zombie_worker_pid_treated_as_non_live_and_does_not_block_dispatch`
  (pre-existing) continues to pass unchanged.

Full suite: see `evidence.json` for exact commands and results.
