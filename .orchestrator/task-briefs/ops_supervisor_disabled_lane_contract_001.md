# OPS-SUPERVISOR-DISABLED-LANE-CONTRACT-001

## Objective

Repair four stale or non-hermetic supervisor tests so the committed disabled
provider-lane policy and the regression suite describe the same behavior.

This task changes tests only. It does not authorize scheduler, provider,
configuration, runtime-state, or product changes.

## Assignment

- Owner: `Antigravity`
- Reviewer: `Codex2`
- Merge target: `dev`
- Auto-merge: disabled
- Planning authority:
  `docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/OPS_SUPERVISOR_DISABLED_LANE_TEST_CONTRACT_PLAN_2026-07-16.md`

The owner and reviewer must be different admitted identities. The planner does
not implement the test patch.

## Confirmed baseline

- Commit `db3ea4a07406ec8e9994f24947c4546371347747` intentionally placed Claude,
  Claude2, Antigravity, Antigravity2, and Copilot in
  `ready_dispatcher.disabled_agents` and set their new-work capacities to zero.
- Three older assertions still require non-zero Claude and Antigravity
  capacities.
- `test_unregistered_runtime_config_agent_is_not_eligible_for_sidecars` uses a
  synthetic empty worker state but leaves the host OS duplicate-worker scan
  enabled; the result therefore changes when a real Codex process is running.
- On the planning base, the combined activity-audit control suite reports
  `494 passed, 4 failed`; all four failures are in these baseline assertions.

## Owned artifact

- `.orchestrator/test_supervisor.py`

No other file may change. In particular, do not modify:

- `.orchestrator/config.json`
- `.orchestrator/supervisor.py`
- canonical status, activity, archive, or runtime files
- BFF, frontend, broker, trading, deployment, or product artifacts

## Required behavior

1. Disabled-lane config tests assert exact membership and zero target,
   per-agent capacity, and quota-group capacity for the disabled lanes.
2. Enabled Codex and Codex2 capacity assertions remain positive and exact.
3. The synthetic roster test explicitly removes host-process nondeterminism by
   setting `worker_os_duplicate_guard` to false in its local fixture or by a
   narrow mock of `scan_live_worker_pids_by_agent`.
4. The synthetic roster test still proves registered Codex, Claude, and Gemini
   are returned and unregistered Qwen is not.
5. Existing production duplicate-worker guard tests remain unchanged and
   passing.
6. At least one test proves a disabled agent is excluded from automatic
   sidecar eligibility.

## Required verification

Run with a fresh repo-external `PANTHEON_STATUS_ROOT`:

- the four named failures;
- full `.orchestrator/test_supervisor.py`;
- adjacent `.orchestrator/test_runtime_state.py`,
  `.orchestrator/test_supervisor_watchdog.py`, and
  `.orchestrator/test_worker_runner_heartbeat.py`;
- `python3 -m py_compile .orchestrator/test_supervisor.py`;
- `git diff --check`.

Record before/after hashes for the task-worktree and central activity logs to
prove the test-only task did not mutate them.

## Delivery

- Start from the exact merged planning head and compose latest `origin/dev`
  before final handoff.
- Commit only the owned test file with required trailers.
- Push a task branch and open a PR to `dev`; keep auto-merge off.
- Codex2 independently inspects the exact head, reruns the required tests, and
  verifies that no lane was re-enabled and no production guard was weakened.
- Owner must not approve or merge its own change.

## Completion

Complete only after independent exact-head approval, green checks, merge, and a
post-merge replay showing the full supervisor suite passes with a live Codex
process present. The activity-audit task must then rebase and pass its complete
required suite before it can proceed.
