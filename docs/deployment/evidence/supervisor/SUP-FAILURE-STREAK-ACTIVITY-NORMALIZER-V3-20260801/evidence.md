# SUP-FAILURE-STREAK-ACTIVITY-NORMALIZER-V3-20260801 — review pending

Task: Normalize governed progress from real activity event shapes

Owner: Codex2 · Reviewer: Antigravity · Status: **review_pending**

## Scope

This task adds only a pure activity normalizer over the exact V3 failure record
merged by PR #4445. It does not wire retry eligibility or consumption and does
not mutate canonical task state, runtime state, queues, workers, leases,
reservations, provider state, configuration, product controllers, or live
services.

## Delivered contract

- Exact-decode the V3 aggregate and bind only its latest immutable failure
  generation.
- Require an exact lowercase 40-hex rejected-head baseline, independent
  owner/reviewer identities, and a logical provider matching the unchanged
  owner.
- Normalize governed reviewer `reopen` only when the actor is the recorded
  reviewer, the `ai-status-event-<sha256>` identity recomputes exactly, and the
  event strictly postdates the failure. When a production GitHub review bridge
  is present, its reopen decision, actor, timestamp, and head must bind the
  rejected failure head.
- Normalize governed `worker_commit` only when the actor is the recorded owner,
  the commit is lowercase 40-hex, the event id is exactly
  `worker-commit-<commit>`, and the commit differs from the rejected head.
- Reject all handoff rows because the current production command emits only
  owner-to-reviewer handoff activity; that is not independent reviewer
  progress.
- Return a scalar-only immutable mapping with a stable progress generation id,
  failure generation id, exact event identity, actor, timestamp, provider, and
  exact head (or `ABSENT` for a governed non-PR reopen).

The allow probes copy the real Human/Ops reopen event
`ai-status-event-442bd286...` and Antigravity worker-commit event
`worker-commit-b77ac064...` from the canonical activity audit without adding
synthetic owner/reviewer fields that production does not emit.

## Verification

- Focused normalizer suite: 5 passed, 477 deselected, 28 subtests passed.
- Full supervisor regression: 482 passed, 94 subtests passed.
- Direct captured-shape node probes: 2 passed.
- Python compile check: passed.
- `git diff --check origin/dev..HEAD` and worktree diff check: passed.
- Rejected PR #4442 heads `e89d09ea`, `67e8ce11`, `1041551b`, `85b4d860`,
  `5b2c309e`, and `69e69ffa` are absent from the candidate ancestry.

## Review boundary

Implementation anchor: `51b45f485dea47ebbea41157fd8d81c40ded3745`.

This evidence is owner-authored and remains **review_pending**. Antigravity must
independently inspect the final PR head and bind the exact-head canonical review
gate before approval or merge. No test count, hosted claim, or approval state is
inferred beyond the commands recorded above.

Generated task briefs, dashboard bundles, assistant packets, and runtime state
that appeared during supervisor synchronization are deliberately excluded from
the task commit.
