# M7 — composition finish and completion review (DTG-CLEAN, 2026-08-31)

## `run_once` and `ai_status.main()`: already the required shape

SD.md 7.10 asks M7 to "reduce `run_once` to a short explicit coordinator
invoking named phases in the existing order" and to leave `ai-status`
holding only "parser/routing, path/runtime binding, public actor/lease
admission, transaction opening, and final projection invocation."

Both entry points already had this shape before M7 started, independent of
M1–M6: `run_once` (`.orchestrator/supervisor.py`) is a linear sequence of
~20 named `_safe_phase("phase_name", fn, ...)` calls in a fixed order —
snapshot loads, one dispatch-plan build, one reservation transaction, the
delivery-launch loop, maintenance, finalize, lifecycle reporting — with no
data-driven phase table to add or remove (SD.md explicitly says not to add
one). `main()` (`scripts/ai_status.py`) does exactly the four things listed:
validate path/runtime binding, parse `argv` into a `commands` dict and
dispatch, open the canonical lock/transaction triple
(`runtime_state_lock` → `canonical_task_state_lock` →
`authoritative_task_state_transaction`) around each mutation, and call
`sync_all(...)` for the final projection. Neither function needed further
extraction for M7 — the M1–M6 waves already moved everything that had a
canonical owner and a pure boundary; what both retain is the
signals/singleton/transaction-orchestration/phase-invocation and
parser/routing/transaction-opening SD.md says must stay.

## Completion-review checklist

**One owner per concern.** A targeted AST scan compared every one of the
116 functions moved across M1–M6 against their old and new locations: 114
have exactly one real body (the new canonical owner) plus, where needed, a
short lazy-handback wrapper (`_supervisor_module()`/`_ai_status_module()`
style, ≤6 lines, no independent logic) in a module that still needs the
symbol but isn't its owner. Two names were flagged with a real body in two
places and manually verified:

- `pid_is_alive` — `supervisor.py`'s own 25-line implementation
  (`os.waitpid` + `/proc/{pid}/stat` + `os.kill(pid, 0)`) and
  `rewrite/status_projection.py`'s 11-line implementation (delegates to
  `proc_pid_state`) are **materially different, independently-authored
  functions that predate this whole effort** — `ai_status.py` and
  `supervisor.py` are separate processes that each already had their own
  process-liveness check before DTG-CLEAN started. M1 correctly moved
  `ai_status.py`'s copy to `status_projection.py`; M4 correctly left
  `supervisor.py`'s copy in place and gave `worker_workspace.py` a lazy
  wrapper reaching *supervisor's* copy specifically, because the moved
  worktree code originally called supervisor's version. This is real,
  pre-existing cross-tool duplication between the two monoliths, not
  something any wave introduced — and out of scope: SD.md's mandate is
  within-monolith decomposition to canonical owners, not merging
  `ai_status.py` and `supervisor.py`'s independent implementations of the
  same concept into one shared library.
- `task_has_active_worker_recovery` — `ai_status.py` has its own 25-line
  implementation reading the same `worker_recovery` task pointer shape
  supervisor.py's (moved-in-M5) 19-line version reads. Same pattern, same
  root cause, same conclusion: pre-existing, independent, out of scope.

Both are noted here as a known finding rather than silently left
undocumented, per this section's own "documented production imports"
requirement, and are reasonable candidates for a future, separately-scoped
task if the two tools' process-liveness/recovery-pointer checks should
ever be unified.

**No duplicate/forwarding-only module.** None of the six modules M1–M6
touched (`rewrite/status_projection.py`, `development_bridge/
dev_bridge_materialize.py`, `rewrite/task_contract.py`, `rewrite/
worker_workspace.py`, `rewrite/worker_recovery.py`, `dispatch_policy.py`)
is itself a forwarding shim — each holds substantial, genuinely-owned
logic. Individual lazy-handback *functions* within them (5–13 per module,
all named `_<module>_module()` plus a small number of same-named
one-line-body wrappers) are a deliberate, narrow exception to "no
forwarding body," used only where a symbol is shared with unrelated
supervisor.py/ai_status.py concerns and genuine ownership transfer would
require a change beyond this effort's scope (e.g. `write_activity_log`,
`pid_is_alive`, `parse_runtime_timestamp` in `worker_workspace.py`;
`task_current_dispatch_responsibility` in `worker_recovery.py`;
`_admission_health_records`, `build_dispatch_event`,
`runtime_delivery_health`, and nine others in `dispatch_policy.py`). Every
one of these forwards to exactly one real implementation; none duplicates
a body.

**No new import cycle.** Verified per-wave at extraction time (each new
module imports cleanly standalone before being wired into its consumer)
and again here with a single fresh-process import of every touched module
together:

```python
import supervisor
import ai_status
from rewrite import status_projection, task_contract, worker_workspace, worker_recovery, dispatch_admission
from development_bridge import dev_bridge_materialize
import dispatch_policy
```

This succeeds, and identity checks confirm the re-exports are the same
object as the canonical owner's, not a copy:
`supervisor.evaluate_dispatch_candidate is dispatch_policy.evaluate_dispatch_candidate`,
`ai_status.write_current_work is status_projection.write_current_work`,
`supervisor.prepare_worker_workspace is worker_workspace.prepare_worker_workspace`,
`supervisor.build_lost_lease_receipt is worker_recovery.build_lost_lease_receipt`
all hold.

**Documented production imports.** Every new/extended module carries a
header docstring stating what it owns, why any lazy handback exists, and
(where relevant, as of M7) which consumers outside supervisor.py/
ai_status.py depend on it and what that implies for isolated-copy test
fixtures (`dispatch_policy.py`'s docstring, added in M7, is the one gap
this review found and closed — it previously had no module docstring at
all despite `development_bridge/dev_bridge_models.py` depending on it).

**Material source reduction attributable to moved behavior**, by wave
(exact `git diff` line counts from each wave's own commit, not a raw
before/after file-size delta, which would be confounded by unrelated
concurrent fleet activity landing on `dev` throughout this effort):

| Wave | Owner | Lines moved out of the monolith |
|---|---|---|
| M1 | `rewrite/status_projection.py` | 1,296 (from `ai_status.py`) |
| M2 | `development_bridge/dev_bridge_materialize.py` | 487 (from `ai_status.py`) |
| M3 | `rewrite/task_contract.py` | 894 (from `ai_status.py`) |
| M4 | `rewrite/worker_workspace.py` | 1,593 (from `supervisor.py`) |
| M5 | `rewrite/worker_recovery.py` | 176 (from `supervisor.py`) |
| M6 | `dispatch_policy.py` | 313 (from `supervisor.py`) |
| **Total** | | **4,759 lines** |

`ai_status.py`: 2,677 lines moved to canonical owners across M1–M3.
`supervisor.py`: 2,082 lines moved to canonical owners across M4–M6.

## Remaining file sizes: ownership explanation

As of this commit, `scripts/ai_status.py` is 9,032 lines and
`.orchestrator/supervisor.py` is 15,154 lines — both above SD.md's 4,000-line
threshold, which the design document itself says "is not a failure by
itself" above that line, provided there is an ownership explanation.

Both files are the sole entry-point coordinators for their respective
tools, and SD.md's own per-wave carve-outs are what keeps them large:

- **`supervisor.py` retains** (verbatim, from M4/M5/M6/M7's own text):
  timing and persistence around worktree operations (M4); process
  observation, termination, and bounded external I/O for worker recovery
  (M5); snapshot load, reservation transaction, worker launch, and result
  persistence for dispatch (M6); and signals, singleton enforcement,
  config/dependency wiring, transaction orchestration, phase invocation,
  and lifecycle reporting for the cycle loop itself (M7). None of that is
  a pure function with a natural canonical owner elsewhere — it is
  imperative process/lock/subprocess/transaction management specific to
  being *the* supervisor process, and moving it would not reduce total
  system complexity, only relocate the exact same stateful orchestration
  behind another lazy-import boundary for no behavioral benefit.
- **`ai_status.py` retains** parser/routing for ~20 CLI subcommands, path
  and runtime-command-root binding validation, the canonical lock/
  transaction triple every mutation opens, and the final projection-sync
  call — again, per M7's own text, exactly what should stay.

Every symbol with a genuine pure-function shape and an existing or
newly-justified canonical owner has been moved; every symbol still in
these two files fits one of the categories the design document itself
says must remain. No further wave is recommended by this review.

## Full validation (cumulative, run against the M6 merge commit before this
commit)

- `test_supervisor.py`: 218/219 (one pre-existing, change-independent
  `ReviewDecisionIntentLeaseRecoveryTests` failure, confirmed via
  git-stash comparison against a clean `dev` tip during M4, unaffected by
  every subsequent wave).
- `test_supervisor_watchdog.py`, `test_supervisor_recovery_process_e2e.py`,
  `rewrite/test_worker_workspace.py`, `rewrite/test_worker_recovery.py`,
  `rewrite/test_task_contract.py`, `scripts.test_ai_status`,
  `scripts.test_status_command_runtime_pin`,
  `scripts.test_check_config_drift`, `scripts.test_component_boundary`:
  725/725.
- `test_dispatch_policy.py`: 30/30. `rewrite/test_dispatch_admission.py`:
  20/20 (untouched module).
- `development_bridge` pytest suite: 76/76 on the committed tree.
- Tooling integration-authority gate
  (`test_auto_integrator`/`test_task_review_merge_gate`/
  `test_github_review_bridge`/`test_run_auto_integrator`/
  `test_auto_integrator_install`/`test_supervisor_launch_ownership`/
  `test_supervisor_watchdog_install`): 275 passed, 7 subtests passed.
- pyflakes clean on every touched module across all six waves (zero new
  findings versus a pristine-tree diff at each step).
- Every merge commit (M3 through M6) promoted to the live supervisor
  runtime and confirmed healthy (`alive: true`,
  `task_state_projection.ok: true`, `source_commit` matching the exact
  merged sha) before proceeding to the next wave.
