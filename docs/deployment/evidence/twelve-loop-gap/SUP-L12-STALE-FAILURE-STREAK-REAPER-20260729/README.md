# SUP-L12-STALE-FAILURE-STREAK-REAPER-20260729

This packet records the current Supervisor Authority V2 repair for the stale
L12 `missing_process` dispatch blocker.

The original task implementation used a bounded failure-streak reaper. PR #4590
merged that implementation, but the branch-content restore reverted the
oversized merge and Authority V2 subsequently retired the failure-streak
control plane. Current runtime loading deletes the obsolete
`provider_guardrails.task_failure_streaks` bucket, and the sole planner does not
consult it. Reintroducing the old reaper would violate the V2 single-authority
source guard.

The current repair therefore closes the remaining routing gap at the V2
assignment boundary:

- raw legacy `missing_process` streak residue cannot suppress the currently
  assigned Claude2 or Antigravity lane;
- `L12-*` and `SUP-L12-*` tasks try their declared `preferred_lane_order`
  before generic Codex-family reassignment fallbacks in both the planner and
  the sole durable owner/reviewer recovery path; and
- non-L12 tasks retain the existing configured fallback order.

No supervisor config, provider readiness, quota/auth handling, product service,
or live runtime state is changed. The machine-readable manifest is
[`evidence.json`](evidence.json), and the current delivery is PR #4795.
Independent review by Antigravity is pending; the reviewer must bind the exact
reviewed PR head and record the decision before owner closeout.
