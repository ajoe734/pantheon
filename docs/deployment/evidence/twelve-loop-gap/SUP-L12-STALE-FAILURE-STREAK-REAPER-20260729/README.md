# SUP-L12-STALE-FAILURE-STREAK-REAPER-20260729

This packet records the implementation and local verification of the durable
stale L12 `missing_process` failure-streak reaper.

The policy removes at most four blocking streaks per supervisor cycle, and only
when all of these facts hold:

- the task is `L12-*` or `SUP-L12-*`;
- the streak has reached the failure-loop threshold;
- the failure kind is exactly `missing_process`;
- the failed lane is still the task's assigned owner or reviewer;
- the task's canonical `last_update` is newer than `last_failure_at`; and
- no matching active worker or pending delivery exists.

Fresh failures, quota/auth failures, non-L12 tasks, unassigned-lane records, and
records with runtime evidence remain fail-closed.

Implementation is in rebased anchor commit
`9d53a94a265c55af4c8d15c50ab3751f1440ac0f`. The branch also includes a
review-stage redispatch regression for the observed Claude2 reviewer lane
failure shape. The machine-readable manifest is [`evidence.json`](evidence.json).
Independent review by Antigravity is pending; the reviewer must record the exact
reviewed head and decision in the manifest before approval is used for owner
closeout.
