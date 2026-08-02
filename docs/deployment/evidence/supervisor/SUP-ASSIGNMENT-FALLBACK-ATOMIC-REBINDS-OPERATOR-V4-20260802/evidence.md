# SUP-ASSIGNMENT-FALLBACK-ATOMIC-REBINDS-OPERATOR-V4-20260802 evidence

Task: Restore atomic task-scoped quota/auth fallback after post-merge coordinator race

Owner: Codex · Reviewer: Human/Ops · Status: **review pending**

## Authoritative source chain

- PR #4499 exact head `d40b73adabec3eba27f6fa111586fc956d7bfe74` passed the Pantheon canonical review gate and merged account-neutral dispatch governance to `dev` as `c8bfca4f3e40559c5d04b3eec4c2e6ae69681c65` before this task branch was created.
- The stale V3 assignment task is archived `superseded` by this V4 because only its dependency record was corrupted after that merge.
- The later V5 coordinator packet is archived `superseded`: it contradicted the explicit operator constraint by trying to impose hardcoded Codex/Codex2 account equivalence and a global mutual-review ban after PR #4499 had already merged.
- Closed, unmerged PR #4496 and `f33ee38bb6bfc684cbb2be62e1751f54189aa6fe` supply implementation design only. V4 does not inherit their status, review, checks, or commit trailers.

## Delivered contract

- Automatic reassignment now plans a complete viable owner/reviewer pair. A viable incumbent reviewer can become owner when the unavailable owner's fallback points to that reviewer; another viable reviewer is selected before any canonical write.
- Direct configured fallbacks retain declared order, task-preferred lanes are included, and discovered mapping edges are traversed breadth-first. A case-insensitive seen set makes fallback cycles finite and deterministic.
- Mainline normalization, helper claims, and both owner- and reviewer-side worker-failure fallback all consume `plan_task_assignment_pair()` rather than rebuilding incompatible field-by-field choices.
- Reviewer failure keeps the incumbent owner fixed, excludes the failed reviewer even when a cycle reaches it again, and limits L12 selection to the bounded candidates that survive provider-first filtering.
- `persist_task_reassignment()` accepts the expected owner, reviewer, and task status. Those values are compared under the canonical task-state lock before both assignment fields are written together.
- Catalog-locked assignment contracts remain immutable. If no distinct viable pair exists or compare-and-swap inputs are stale, canonical state is not mutated.
- Independence remains distinct configured agent identity only. Codex and Codex2 may review one another; no account group, quota group, provider policy, config, or global mutual-review ban changes in this task.

## Incident reproduction

The focused regression creates the reported 23 `todo` tasks with unavailable Antigravity owners and Codex2 incumbent reviewers. The viable owner fallback is Codex2 while Claude is unavailable. All 23 are planned as `owner=Codex2, reviewer=Codex` and persisted with owner/reviewer/status compare-and-swap expectations. Separate owner-side and reviewer-side no-pair tests plus stale-CAS coverage prove zero canonical mutation. Reviewer-failure coverage traverses a cyclic graph deterministically and verifies exact owner/reviewer/status expectations on persistence.

## Scope boundaries

The task changes only supervisor assignment planning/persistence, supervisor tests, the task brief, and this evidence directory. It does not edit runtime state, canonical status JSON by hand, catalog contracts, account or quota groups, provider readiness policy, global reviewer policy, config, product code, services, deployment, or the 28-task catalog.

## Owner verification

- Both canonical prerequisites are archived `done/completed`.
- Checkout-scoped Python distribution provisioned and verified.
- Focused assignment regression: 40 passed, 475 deselected in 3.59s.
- Full supervisor regression: 515 passed, 147 subtests passed; JUnit recorded 662 cases with zero failures or errors.
- Python compile, evidence JSON parse, commit trailers, `git diff --check`, exact-head GitHub CI, and independent review remain mandatory gates.

## Review and delivery

Review remains pending independent Human/Ops inspection of PR #4501's exact GitHub `headRefOid`. The governed handoff and approval bind that head because this committed manifest cannot self-reference its containing commit. Delivery targets `ajoe734/pantheon` branch `dev`; merge and exact-head review identities will be recorded only after the governed review gate completes. Rollback is a revert of the V4 merge commit; no state or config migration is required, and source merge does not authorize live rollout.
