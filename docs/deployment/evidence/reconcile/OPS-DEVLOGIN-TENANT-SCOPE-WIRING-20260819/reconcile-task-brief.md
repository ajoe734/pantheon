# Task Brief: OPS-DEVLOGIN-TENANT-SCOPE-WIRING-20260819

- Status: review_approved
- Owner: Claude
- Reviewer: Antigravity
- Repository: ajoe734/pantheon
- Delivery PR: #5045
- Delivery commit: 94549f44dd09d057f13034c14963b69173e34114

## Why this reconciliation exists

The task's canonical row was rolled back from `review_approved` to
`in_progress` by a stale supervisor recovery action that raced with an
already-completed delivery, not by any defect in the delivery itself.
Timeline:

- `2026-08-19T14:10:18Z` — commit `22e3a5609bf4a3529d2aa67afbb35ae8d49da437`
  authored (docker-compose.yml dev-login tenant passthrough fix).
- `2026-08-20T00:19:42Z` — independent review approved by Antigravity;
  GitHub review-proof tag `pantheon-review/approve/22e3a5609bf4a3529d2aa67afbb35ae8d49da437`
  created, bound to PR #5045 exact head `22e3a5609`.
- `2026-08-20T00:26:37Z` — a Human/Ops `reopen` (issued for an unrelated,
  earlier "worker process missing during boot reconciliation" signal
  that predated the approval and had already gone stale) rolled the task
  row back to `in_progress`, racing with the delivery that had, in
  reality, already been reviewed and was merging.
- `2026-08-20T00:28:02Z` — PR #5045 merged into `dev` as commit
  `94549f44dd09d057f13034c14963b69173e34114`.

The delivery is genuine, reviewed, and already on `dev`. The task branch
is now deleted (post-merge), so a fresh handoff/approve cycle on this
task is no longer possible. This document lets a Human/Ops actor
reconcile the task to `done` through the explicit, audited
`reconcile_merged_done` recovery path instead of leaving a correctly
delivered task permanently stuck on a stale `in_progress` row.
