# SUP-L12-STALE-FAILURE-STREAK-REAPER-20260729 evidence

Date: 2026-07-29

## Architecture finding

The L12 fleet was not blocked by Pantheon/Agora product code or by an immediate
dev deployment failure. The blocker was in the supervisor control plane:

- task rows were ready for Claude2/Antigravity review or owner work;
- provider readiness reported the lanes as authenticated and local-worker
  capable;
- no matching live worker or pending queue delivery existed for several ready
  rows;
- stale `missing_process` task failure streaks from supervisor boot
  reconciliation still reached the chair/failure-loop threshold and blocked
  redispatch.

This is a control-plane truth split: task-board truth, provider readiness truth,
worker runtime truth, and dispatch/failure-loop truth disagreed. Clearing or
reassigning a single task would only mask the symptom.

## Change

The supervisor now performs a bounded stale-streak reaper before building the
failure-loop blocker map used by ready dispatch.

It clears only task failure streaks that satisfy all of these conditions:

- `last_failure_kind == "missing_process"`;
- the task still exists and is currently dispatchable to the same assigned
  owner/reviewer;
- the provider is currently eligible to take that task;
- no matching active worker or non-terminal pending queue event exists.

It deliberately does not clear auth, quota, terminal, or generic-exit streaks.

## Files

- `.orchestrator/supervisor.py`
- `.orchestrator/test_supervisor.py`
- `docs/deployment/evidence/twelve-loop-gap/SUP-L12-STALE-FAILURE-STREAK-REAPER-20260729/README.md`

## Validation

Targeted regression:

```bash
PYTHONPATH=.orchestrator python3 -m unittest \
  test_supervisor.ProcessQueueDispatchGuardTests.test_dispatcher_clears_stale_missing_process_streak_for_ready_l12_review \
  test_supervisor.ProcessQueueDispatchGuardTests.test_dispatcher_keeps_terminal_quota_streak_blocking_review \
  test_supervisor.ProcessQueueDispatchGuardTests.test_dispatcher_prioritizes_l12_review_over_unrelated_review_for_claude2
```

Result:

```text
Ran 3 tests in 0.010s
OK
```

Full supervisor regression:

```bash
PYTHONPATH=.orchestrator python3 .orchestrator/test_supervisor.py
```

Result:

```text
Ran 459 tests in 14.794s
OK
```

## Non-goals

- No `.orchestrator/config.json` edit.
- No Pantheon or Agora product code change.
- No deployment switch.
- No bypass of auth, quota, review, root-freeze, or GitHub canonical review
  gates.
