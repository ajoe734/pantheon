# OPS Supervisor Disabled-Lane Test Contract Plan — 2026-07-16

## Purpose

Restore the control-plane regression baseline required by
`OPS-ACTIVITY-AUDIT-LEGACY-OVERLAP-RECOVERY-001` without changing the live
scheduler policy or reopening an exhausted provider lane.

This is a planning artifact. The planner may author and archive this plan,
dispatch the execution task, and review the result. The assigned fleet owns
all implementation and test changes.

## Confirmed gap

The full control-plane suite on `origin/dev` currently has four deterministic
contract failures in `.orchestrator/test_supervisor.py`:

1. three tests still require the pre-2026-07-05 Claude, Claude2, Antigravity,
   and Antigravity2 workload and concurrency values;
2. the committed runtime configuration has intentionally disabled those lanes
   and set their workload, per-agent capacity, and quota-group capacity to
   zero in commit `db3ea4a07406ec8e9994f24947c4546371347747`;
3. one synthetic underutilization test leaves the real OS worker-duplicate
   scan enabled, so a live Codex process can remove Codex from its expected
   result even though the fixture contains no worker.

The activity-audit candidate does not modify `.orchestrator/config.json`,
`.orchestrator/supervisor.py`, or these failing assertions. The same config and
test mismatch exists on `origin/dev`; it must not be hidden as an allowed
baseline failure because the activity-audit acceptance contract requires the
complete supervisor suite to pass.

## Authority and intended behavior

- The committed `ready_dispatcher.disabled_agents` list is the authoritative
  durable admission policy.
- A lane in that list must have zero new-work target, zero per-agent capacity,
  and zero quota-group concurrency in this snapshot.
- This repair must not remove a disabled lane, raise a zero, change fallback
  routing, or start a worker.
- Re-enabling a lane requires a separate readiness decision with current auth,
  quota, provider-capability, and rollout evidence. It is not authorized here.
- Unit tests using a synthetic runtime state must not inspect unrelated live
  worker processes on the host. The fixture must explicitly disable or mock
  the OS duplicate guard while retaining separate coverage of that guard.

## Execution task

Task: `OPS-SUPERVISOR-DISABLED-LANE-CONTRACT-001`

- Owner: Antigravity
- Reviewer: Codex2
- Repository: `pantheon`
- Merge target: `dev`
- Auto-merge: off
- Implementation scope: `.orchestrator/test_supervisor.py` only
- Production code/config changes: prohibited

## Required implementation

1. Replace stale enabled-capacity assertions with assertions that bind the
   exact disabled-lane contract: membership in `disabled_agents`, zero
   `target_workload`, zero `max_tasks_per_agent_by_agent`, and zero matching
   quota-group capacity.
2. Keep positive capacity assertions for enabled Codex and Codex2 lanes so a
   blanket all-zero configuration cannot pass.
3. Make the unregistered-runtime-agent sidecar test hermetic by disabling the
   OS duplicate guard in that synthetic fixture or by narrowly mocking the OS
   process scan. Do not weaken the production duplicate guard.
4. Preserve the test's actual purpose: rostered Codex, Claude, and Gemini are
   eligible under the synthetic fixture while unregistered Qwen is rejected.
5. Add or retain an explicit negative assertion that a configured disabled
   agent is not eligible for automatic sidecar dispatch.

## Verification gates

- the four previously failing tests pass when real Codex worker processes are
  present;
- the entire `.orchestrator/test_supervisor.py` suite passes from a repo-external
  isolated `PANTHEON_STATUS_ROOT`;
- the activity-audit required suite passes after the activity candidate is
  rebased onto the merged repair;
- `python3 -m py_compile .orchestrator/test_supervisor.py` and
  `git diff --check` pass;
- the execution diff contains no production source, config, status, runtime,
  evidence, or product file;
- independent Codex2 exact-head review confirms the assertions match committed
  policy and are not merely changed to make tests green.

## Delivery order

1. Merge this plan and task brief.
2. Dispatch the task to Antigravity in a clean task worktree based on the exact
   merged planning head.
3. Review and merge the test-contract repair with auto-merge disabled.
4. Rebase the activity-audit candidate onto the new `dev` head and rerun all
   required control-plane suites.
5. Only then may the activity-audit candidate proceed to independent review.

## Completion definition

This prerequisite is complete only when the scoped repair PR is independently
approved and merged, the full supervisor suite is green in an isolated root,
and the activity-audit candidate passes its complete required suite after
rebasing onto that merge.
