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

Both call sites that need worker-wrapper identity now share the tightened
`cmdline_is_worker_runner(parts)` predicate instead of each keeping — and
subtly diverging from — their own substring check:

- `supervisor.scan_live_worker_pids_by_agent` (fleet capacity / live-worker
  count reconciliation)
- `supervisor._proc_worker_runner_launch_marker` (launch-recovery identity
  evidence used to recover an intent's exact live PID/start-tick generation)

`cmdline_is_worker_runner` binds directly to the actual interpreter or script
invocation, rather than searching anywhere in argv:

- **Direct script execution**: `argv[0]` is itself path-shaped, contains no
  whitespace, has `Path(argv[0]).name == "worker_runner.py"`, and has
  `.orchestrator` among `Path(argv[0]).parts`.
- **Python interpreter execution**: `argv[0]` is a recognized Python or PyPy
  executable (e.g. `python3`, `python`, `python3.12`), followed by optional
  Python interpreter flags (e.g. `-u`, `-B`, `-W ignore`, `-Wignore`, `-W ""`, `-X dev`,
  `-Xdev`, `-X ""`, `--check-hash-based-pycs`; excluding stdin mode `-` and inline code/module
  modes `-c`/`-m`), and the first positional script argument is path-shaped, contains no
  whitespace, has `Path(token).name == "worker_runner.py"`, and has
  `.orchestrator` among `Path(token).parts`.

This explicitly rejects:
- Provider CLI arguments (e.g. `['claude', '--prompt', '/repo/.orchestrator/worker_runner.py', 'wake']`),
  where `argv[0]` is the provider binary rather than Python.
- Bubblewrap sandbox bind operands (e.g. `['/usr/bin/bwrap', '--ro-bind', '/repo/.orchestrator/worker_runner.py', '/tmp/ref.py', 'wake']`),
  where `argv[0]` is the sandbox binary and the script path is a mount argument.
- Non-script Python modes:
  - Stdin script execution (`-` and `-u -`).
  - Inline code execution (`-c`, `-c<code>`, clustered/attached `-uc<code>`, etc.).
  - Module execution (`-m`, `-m<mod>`, clustered/attached `-um<mod>`, etc.).
- Python executions of other scripts where `worker_runner.py` is an argument
  to that other script (e.g. `['python3', '/repo/other.py', '/repo/.../worker_runner.py']`).
- Option-argument misattributions where `worker_runner.py` is consumed as the parameter
  to a preceding option (e.g. `['python3', '-W', '/repo/.../worker_runner.py']`).
- Empty-script Python executions where `worker_runner.py` is an argument to an empty script token
  (e.g. `['python3', '', '/repo/.../worker_runner.py']`).
- Prompt text arguments merely quoting `worker_runner.py` as free text.

Procfs `/proc/<pid>/cmdline` parsing strips only the single terminating NUL separator
and preserves interior empty argv tokens so that option arguments like `-W ""` are retained
and empty script tokens `""` are not skipped.

Both capacity scanning and launch-recovery identification now use this one
predicate, so a real wrapper is counted/identified exactly once and descendants
that carry the same wake prompt or pass the script as an argument/operand are
excluded from both.

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
tree.

A reproducible read-only observation was captured from the live coordination
root via:

```bash
python3 -c '
import json, datetime
from pathlib import Path

status_root = Path("/home/chloe_ong_dev_cctech_support_com/pantheon-ci-deploy/coordination-root")
state_path = status_root / ".orchestrator/worker-runtime/state.json"
watchdog_path = status_root / ".orchestrator/watchdog-state.json"

with open(state_path, encoding="utf-8") as f:
    state = json.load(f)
with open(watchdog_path, encoding="utf-8") as f:
    watchdog = json.load(f)

sup = state.get("supervisor", {})
cmd_health = sup.get("command_runtime_health", {})
runtime_info = cmd_health.get("runtime", {})
res = watchdog.get("last_decision", {}).get("resource", {})

observation = {
    "observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "read_only_health": {
        "supervisor_pid": sup.get("pid"),
        "lifecycle": sup.get("lifecycle"),
        "last_heartbeat_at": sup.get("last_heartbeat_at"),
        "last_successful_loop_at": sup.get("last_successful_loop_at"),
        "command_runtime_healthy": cmd_health.get("healthy"),
        "command_runtime_reason": cmd_health.get("reason"),
    },
    "counts": {
        "watchdog_active_worker_count": res.get("active_worker_count"),
        "watchdog_active_worker_live_count": res.get("active_worker_live_count"),
        "watchdog_active_worker_runtime_state_count": res.get("active_worker_runtime_state_count"),
        "watchdog_active_worker_count_source": res.get("active_worker_count_source"),
        "scheduler_state_workers_count": len(state.get("workers", {})),
    },
    "identities": {
        "supervisor_pid": sup.get("pid"),
        "command_root": runtime_info.get("command_root"),
        "source_sha": runtime_info.get("source_sha"),
        "remote": runtime_info.get("remote"),
        "base_ref": runtime_info.get("base_ref"),
        "coordination_status_root": str(status_root),
    },
    "scheduler_workers": {
        k: {"status": v.get("status"), "task_id": v.get("task_id")}
        for k, v in state.get("workers", {}).items()
    }
}
print(json.dumps(observation, indent=2))
'
```

Observation result:
- `observed_at`: `2026-09-11T01:20:38.054190+00:00`
- `read_only_health`:
  - `supervisor_pid`: 3325540
  - `lifecycle`: `"running"`
  - `last_heartbeat_at`: `"2026-09-11T01:20:14Z"`
  - `last_successful_loop_at`: `"2026-09-11T01:20:09Z"`
  - `command_runtime_healthy`: `true`
  - `command_runtime_reason`: `"healthy"`
- `counts`:
  - `watchdog_active_worker_count`: 1
  - `watchdog_active_worker_live_count`: 1
  - `watchdog_active_worker_runtime_state_count`: 1
  - `watchdog_active_worker_count_source`: `"live_worker_runner_pid_identity"`
  - `scheduler_state_workers_count`: 1
- `identities`:
  - `supervisor_pid`: 3325540
  - `command_root`: `"/home/chloe_ong_dev_cctech_support_com/pantheon-ci-deploy/command-runtimes/ba6c9e99ec4a0b09ca85b30ab18bb862a3e42e58"`
  - `source_sha`: `"ba6c9e99ec4a0b09ca85b30ab18bb862a3e42e58"`
  - `remote`: `"ajoe734/pantheon"`
  - `base_ref`: `"origin/dev"`
  - `coordination_status_root`: `"/home/chloe_ong_dev_cctech_support_com/pantheon-ci-deploy/coordination-root"`
- `scheduler_workers`:
  - `antigravity-20260911T010623Z-ed546b0e` (task: `OPS-SUPERVISOR-WORKER-IDENTITY-CORRECTIVE-001`, status: `running`)

The running supervisor's source SHA predates this fix, so its live process
image still runs the prior scan until an operator promotes the merged commit.

## Verification

Focused procfs regressions (`.orchestrator/test_supervisor.py`):

- `test_scan_live_worker_pids_excludes_prompt_text_and_bwrap_descendants`:
  verifies capacity scanning counts genuine wrapper executions (standard
  python, python with `-u`, direct script invocation, and python with empty option argument `-W ""`) while strictly rejecting
  provider arguments (`claude --prompt <path>`), sandbox bind operands
  (`bwrap --ro-bind <path>`), free-text prompt references, python running
  unrelated scripts, python `-c` code execution (including clustered `-uc<code>`),
  python stdin mode (`-`), empty script arguments (`python3 "" <path>`), and non-orchestrator scripts.
  PID membership comparison is order-independent and retains duplicate detection.
- `test_proc_worker_runner_launch_marker_rejects_descendants_and_bind_operands`:
  verifies recovery rejects provider arguments (`claude --prompt <path>`),
  sandbox bind operands (`bwrap --ro-bind <path>`), free text references,
  python `-c` code execution (`-uc<code>`), python stdin mode (`-`), and empty script arguments (`python3 "" <path>`)
  even when matching `ORCH_TASK_ID`/`ORCH_AGENT_ID`/`ORCH_RUN_ID` are present in
  the process environment.
- `test_proc_worker_runner_launch_marker_recovers_real_wrapper` and `test_proc_worker_runner_launch_marker_recovers_with_empty_option_argument`:
  positive recovery coverage verifying real python worker wrappers (including invocations with empty option arguments like `-W ""`)
  are correctly identified, start ticks validated against the prepared intent,
  and complete recovery marker dictionaries returned.
- `test_cmdline_is_worker_runner_predicate_supported_and_rejected`:
  direct unit testing of the exact predicate against supported wrapper flags
  (`-u`, `-B`, `-W`, `-X`, `--`, including empty option arguments like `-W ""` and `-X ""`) and rejected non-wrapper forms (stdin mode `-`,
  `-c`, `-m`, clustered/attached `-uc`, `-um`, `-cimport`, `-mmod`, `-W <path>`, empty script invocations).
- `test_zombie_worker_pid_treated_as_non_live_and_does_not_block_dispatch`:
  pre-existing zombie filtering continues to pass unchanged.

Full suite: see `evidence.json` for exact commands and results.
