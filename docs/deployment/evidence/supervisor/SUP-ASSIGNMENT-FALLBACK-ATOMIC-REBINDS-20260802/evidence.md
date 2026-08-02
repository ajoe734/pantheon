# SUP-ASSIGNMENT-FALLBACK-ATOMIC-REBINDS-20260802 evidence

Task: Atomically rebind owner and reviewer across fallback dead ends

Owner: Codex · Reviewer: Codex2 · Status: **review pending**

## Delivered contract

- Automatic reassignment now plans a complete viable owner/reviewer pair. A
  viable incumbent reviewer can become owner when the unavailable owner's only
  fallback points to that reviewer; another viable reviewer is selected before
  any canonical write.
- Direct configured fallbacks retain declared order, task-preferred lanes are
  included, and discovered mapping edges are traversed breadth-first. A
  case-insensitive seen set makes fallback cycles finite and deterministic.
- Mainline normalization, helper claims, and worker-failure owner fallback all
  consume `plan_task_assignment_pair()` rather than rebuilding incompatible
  field-by-field choices.
- `persist_task_reassignment()` accepts the expected owner, reviewer, and task
  status. Those values are compared under the canonical task-state lock before
  both assignment fields are written together.
- Catalog-locked assignment contracts remain immutable. If no distinct viable
  pair exists, the planner returns no plan and persistence is never called.
- Independence remains distinct agent identity only. Codex and Codex2 may
  review one another; no account group, quota group, provider policy, or global
  mutual-review ban changes in this task.

## Incident reproduction

The focused regression creates the reported 23 `todo` tasks with unavailable
Antigravity owners and Codex2 incumbent reviewers. The only viable owner
fallback is Codex2, while Claude is unavailable. All 23 are planned as
`owner=Codex2, reviewer=Codex` and persisted with assignment/status CAS
expectations. A separate no-pair test proves zero mutation when only cyclic,
non-independent candidates exist.

## Scope boundaries

The task changes only supervisor assignment planning/persistence, supervisor
tests, the task brief, and this evidence directory. It does not edit runtime
state, canonical status JSON by hand, catalog contracts, account or quota
groups, provider readiness policy, global reviewer policy, product code, or
live services. Dashboard mirrors and assistant packet lock fixtures produced by
the full suite were restored/removed and are not part of the task diff.

## Verification

- Checkout-scoped Python distribution provisioned and verified.
- Focused assignment regression: 38 passed, 475 deselected.
- Full supervisor regression: 513 passed, 147 subtests passed in 53.39s.
- Python compile and `git diff --check` passed.

## Review and delivery

Review is pending independent Codex2 inspection of the exact task PR head.
Delivery targets `ajoe734/pantheon` branch `dev`; merge and exact-head review
identities will be recorded after the governed review gate completes.
