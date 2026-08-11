# OPS-SUPERVISOR-ARCHITECTURE-LIVE-ROLLOUT-20260811 — preflight evidence and blocker

## Status at this checkpoint

Read-only preflight only. No promotion transaction was executed by this
worker. `scripts/promote_supervisor_runtime.py --promote` sends
`SIGTERM`/`SIGKILL` to the live incumbent supervisor PID as part of its
transactional swap (see `scripts/promote_supervisor_runtime.py` around the
`os.kill(generation.pid, signal.SIGTERM)` / `SIGKILL` calls). That is a live
production process replacement of the exact supervisor currently dispatching
this task, matching the class of action recorded as blocked for background
auto workers in `SUP-COMMAND-RUNTIME-REFRESH-001` (harness permission
classifier denies process-control commands from a background worker session,
and there is no exposed approval route). This task also explicitly forbids
"manually signal the process" as an alternative, so there is no substitute
in-scope action.

## Dependency check (acceptance item 1)

Both dependencies are canonical `done` and their merge commits are ancestors
of `origin/dev`:
- `SUP-CANONICAL-PACKET-ATOMIC-MATERIALIZATION-20260811` — merged via PR
  #4754 (`422c50961`).
- `SUP-WORK-CONSERVING-CANONICAL-ROUTING-20260811` — merged via PR #4755
  (`9e023f7ee`), which is the current `origin/dev` tip.

## Preflight inventory (acceptance item 2)

Captured in this directory:
- `preflight-discover-only-20260811T183531Z.json` —
  `python3 scripts/promote_supervisor_runtime.py --repo /home/lupin/pantheon --discover-only --json`.
  This mode "never signals or launches a process" per the script's own
  `--discover-only` help text. Health report shows the live supervisor
  process alive, heartbeat fresh (28s old), lifecycle `running`, and
  `task_state_shadow` caught up (`event_count=16840`,
  `projected_state_sha256 == expected_state_sha256`).
- `live-supervisor-observation-20260811T1835Z.json` — direct `/proc/<pid>`
  cmdline/environ read of the live supervisor process (pid 3811421, started
  2026-08-11T17:58:58Z) plus the `.orchestrator/state.json` supervisor
  summary at the observation timestamp.

## Key finding: live command root already reports the target source

The live supervisor's `PANTHEON_COMMAND_ROOT` /
`PANTHEON_COMMAND_RUNTIME_SHA` already equal `9e023f7eeadce48fbc161de431fe2edbf4e1b805`
— the current `origin/dev` tip containing both required dependency merges.
This worker did not perform that swap. Inspection of
`/home/lupin/pantheon-ci-deploy/runtime/promotion-evidence/` shows an
existing automated promotion pipeline runs `promote_supervisor_runtime.py
--promote` independently of this task (e.g. a genuine, non-test transaction
at `2026-08-11T03:34:15Z` swapped incumbent `0bd7cf884...` to candidate
`7f93be09e...`, and further attempts continued through the day). The three
1333-byte entries around `18:18-18:34Z` are **pytest fixture artifacts**, not
live cutovers: their `requested_evidence_path` points at
`/tmp/pytest-of-lupin/...` and the abort reason
(`"mutable incumbent is not an immutable command runtime"`) is a unit-test
condition. So the live root reaching `9e023f7ee` is most plausibly the
existing automated pipeline's own routine promotion, not an artifact of a
test run and not an action this task took.

## Why this task is blocked here

1. Verifying the remaining acceptance items (>=3 fresh supervisor loops,
   canary dispatch behavior across two full cycles, no duplicate leases,
   etc.) requires sustained live observation windows of 15+ minutes. In this
   task's two prior dispatch attempts (Codex, then Codex helper-claim), the
   supervisor preempted the worker after 15-20 minutes each time to free
   capacity for higher-priority review/finalize work before any of this
   verification could complete — see `ai-activity-log.jsonl` entries at
   `18:04:56Z`/`18:06:04Z` and `18:25:23Z`/`18:25:35Z`.
2. Executing `--promote` itself (even though the live root already matches
   the target, an explicit governed transaction may still be wanted for this
   task's own audit trail) requires killing and relaunching the live
   supervisor process that is currently dispatching this very worker. That
   is exactly the process-control action class blocked for auto workers per
   `SUP-COMMAND-RUNTIME-REFRESH-001`.

## Handoff

Recording a blocker to `Human/Ops` to: (a) confirm whether the
already-observed live command root at `9e023f7ee` satisfies acceptance item
3 given it appears to have landed through the same governed
`promote_supervisor_runtime.py` transactional path (just not one this task
executed), or whether a fresh task-scoped `--promote` run is still required
for the audit trail; and (b) if a fresh `--promote` and multi-cycle live
canary observation is required, run or supervise it directly, since it is a
live production process replacement outside the auto-worker permission
envelope.
