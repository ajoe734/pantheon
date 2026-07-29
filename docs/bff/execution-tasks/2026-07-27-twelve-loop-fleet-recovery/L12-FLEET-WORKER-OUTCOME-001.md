# L12-FLEET-WORKER-OUTCOME-001 — Make missing workers bounded outcomes

Owner: Codex2
Reviewer: Codex
Parallel group: wave-0-control

Repair the worker lifecycle gap where a worker starts, then boot
reconciliation marks the process missing while the task stays in an ambiguous
or misleading state.

Acceptance:

- Missing worker process creates a bounded retry, reopen, or terminal failure
  with task id, run id, provider, and reason.
- Closeout workers that disappear cannot leave `review_approved` tasks looking
  as if owner closeout is still progressing.
- Regression tests cover missing owner and missing reviewer workers.
