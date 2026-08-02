# SUP-FAILURE-STREAK-ACTIVITY-NORMALIZER-V3-20260801 — review pending

Task: Normalize governed progress from real activity event shapes

Owner: Codex · Reviewer: Human/Ops · Status: **review_pending**

## Scope

This task adds only a pure activity normalizer over the exact V3 failure record
merged by PR #4445. It does not wire retry eligibility or consumption and does
not mutate canonical task state, runtime state, queues, workers, leases,
reservations, provider state, configuration, product controllers, or live
services. The task-scoped owner fallback does not combine the separate
Codex/Codex2 account or quota groups and does not change global reviewer
policy.

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

- Focused normalizer suite: 5 passed, 486 deselected, 28 subtests passed.
- Full supervisor regression: 491 passed, 102 subtests passed.
- Direct captured-shape node probes: 2 passed.
- Python compile check: passed.
- `git diff --check origin/dev..HEAD` and worktree diff check: passed.
- Rejected PR #4442 heads `e89d09ea`, `67e8ce11`, `1041551b`, `85b4d860`,
  `5b2c309e`, and `69e69ffa` are absent from the candidate ancestry.

## Review boundary

Implementation anchor: `51b45f485dea47ebbea41157fd8d81c40ded3745`.
Current `origin/dev` base: `16135f2316541591e54cba6d58f0a80273731bc1`.
Non-rewriting composition commit: `802464d9f9ade9e81d57860726cfa44a9eaa5171`.
Pull request: `#4483`.

This evidence is owner-authored and remains **review_pending**. Human/Ops
independently reviewed the prior head `9c68b31a79a8dc6bb48b4eb90bf8d7192daf021d`
and then reopened the gate because `origin/dev` advanced. Human/Ops must inspect
the new final PR head and bind that exact head in the canonical review gate
before approval or merge. No test count, hosted claim, or approval state is
inferred beyond the commands recorded above.

Generated task briefs, dashboard bundles, assistant packets, and runtime state
that appeared during supervisor synchronization are deliberately excluded from
the task commit.
