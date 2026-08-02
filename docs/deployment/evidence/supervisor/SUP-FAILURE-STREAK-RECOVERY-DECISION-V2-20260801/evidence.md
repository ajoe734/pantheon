# SUP-FAILURE-STREAK-RECOVERY-DECISION-V2-20260801 — review pending

Task: Build a pure bounded retry recovery decision matrix

Owner: Codex · Reviewer: Human/Ops · Status: **review_pending**

## Scope

This task adds a pure, fail-closed recovery decision over the exact V3 failure
streak and normalized progress generation contracts already merged into
`dev`. It does not consume the proposed token, clear a failure streak, update
a counter, reserve delivery, enqueue or dispatch work, mutate canonical task
state, change provider policy or configuration, or touch live runtime.

## Delivered contract

- Exact-decode the immutable normalized progress generation and recompute its
  generation identity before it can participate in a decision.
- Permit only `generic_exit` and `missing_process`; all unknown, terminal,
  timeout, parse, exit-code, auth, quota, policy, overlap, worktree, branch,
  task, and provider-unready failure kinds fail closed.
- Require current task status `todo` or `in_progress`, unchanged owner and
  reviewer identities, owner different from reviewer, target agent equal to
  owner, and logical provider equal to the normalized owner identity.
- Require a governed reviewer `reopen` progress generation strictly newer
  than the latest failure generation. A bound review head must equal the
  rejected head; an unbound governed reopen uses `ABSENT`. Owner commits do not
  substitute for independent reviewer progress.
- Derive a stable `failure-recovery:<sha256>` one-shot consumption token from
  the exact task, provider, failure generation, and progress generation, but
  do not record or consume it in this task.
- Deny when the exact task has an active worker, a pending or started queue
  event, a delivery reservation, or an active worktree lease.
- Require an exact logical-provider gate with `ready=true` and no auth, quota,
  or policy pause.
- Return a nested immutable mapping containing allow/deny, reason code, exact
  task/target/provider and failure/progress generation identities, prior count,
  kind and timestamp, qualifying event, and proposed consumption token.

The allow probe uses the production identity model
`owner=Antigravity`, `reviewer=Human/Ops`, and `provider=antigravity`. The
owner-equals-reviewer row is explicitly denied.

## Verification

- Recovery decision suite: 8 passed, 491 deselected, 45 subtests passed.
- Failure-record + activity-normalizer + recovery chain and production call
  sites: 25 passed, 474 deselected, 135 subtests passed.
- Full supervisor regression: 499 passed, 147 subtests passed.
- Python compile check: passed.
- `git diff --check`: passed.
- Rejected PR #4437 heads `07316c73` and `77af55015`, plus rejected PR
  #4438 heads `94e3b6f3`, `d616beaa`, and `35056b71`, are absent from the
  candidate ancestry.

## Review boundary

Implementation anchor: `e84fabefe45f4e53d81d554b56956e45d0ff4e0f`.
Implementation candidate: `dd819f3f2cf40d4c14d34097a95f7cf064a48202`.
Base at implementation verification: `5cb03dd5ebe3ce13435c848175c5dab5450e9ea5`
(`origin/dev`).
Pull request: `#4489`.

This evidence is owner-authored and remains **review_pending**. Human/Ops must
independently review the final exact PR head and bind that head through the
canonical review gate before merge. No approval, live-runtime outcome, or
consumption/dispatch behavior is claimed here.

Rollout is source merge only. Rollback is revert of this task merge commit.
