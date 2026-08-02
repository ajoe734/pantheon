# Task Brief: SUP-ASSIGNMENT-FALLBACK-ATOMIC-REBINDS-OPERATOR-V4-20260802

Generated in the worker workspace because the supervisor root did not have a task brief file; expanded here as the durable V4 implementation and review record.

## Task
- Title: Restore atomic task-scoped quota/auth fallback after post-merge coordinator race
- Status: review_pending
- Owner: Codex
- Reviewer: Human/Ops
- Base: `origin/dev` at `c8bfca4f3e40559c5d04b3eec4c2e6ae69681c65`
- Branch: `task/SUP-ASSIGNMENT-FALLBACK-ATOMIC-REBINDS-OPERATOR-V4-20260802`
- Anchor: `db6e23c3cfe79e6dcdb91c4df86f5eff7ad33062`
- Next: publish the task PR, pass exact-head CI, and obtain independent Human/Ops exact-head review before merge.

## Summary
Recover the already-tested atomic owner/reviewer pair planner under merged account-neutral governance after a stale post-merge coordinator race corrupted the V3 dependency record.

## Authoritative Source Chain

- The explicit operator constraint is authoritative: `Codex` and `Codex2` remain distinct configured identities and quota groups. This task adds neither hardcoded account equivalence nor a global mutual-review prohibition.
- PR #4499 exact head `d40b73adabec3eba27f6fa111586fc956d7bfe74` passed the Pantheon canonical review gate at 2026-08-02T09:12:29Z and merged to `dev` as `c8bfca4f3e40559c5d04b3eec4c2e6ae69681c65` at 2026-08-02T09:12:35Z.
- `SUP-ASSIGNMENT-FALLBACK-ATOMIC-REBINDS-OPERATOR-V3-20260802` was archived with `terminal_outcome=superseded` and `superseded_by` this V4 task because its dependency binding was corrupted after the PR #4499 merge.
- `OPS-CHATBOX-DISPATCH-AUTHORITY-RESTORE-V5-20260802` was archived with `terminal_outcome=superseded`: it was admitted after the approved PR #4499 merge and contradicted the explicit operator constraint.
- Closed, unmerged PR #4496 and source commit `f33ee38bb6bfc684cbb2be62e1751f54189aa6fe` are implementation references only. Their task status, review state, checks, and trailers are not V4 delivery authority.

## Delivered Contract

- Mainline normalization, helper claims, and terminal worker-failure fallback consume one shared owner/reviewer pair planner.
- Configured and task-preferred fallback graphs are traversed in stable breadth-first order with case-insensitive cycle detection.
- A viable incumbent reviewer may become the new owner when the old owner is unavailable; the planner selects another viable, distinct reviewer before any canonical write.
- Governed persistence compares expected owner, reviewer, and status under the canonical task-state lock, then writes both assignment fields together.
- Catalog-locked assignments remain immutable, and a missing legal distinct pair or stale compare-and-swap causes no canonical mutation.
- Independence is based on distinct configured agent identity only. Codex and Codex2 remain valid mutual owner/reviewer assignments unless task-scoped live auth/quota evidence makes one unavailable.

## Verification

- Both canonical prerequisites are archived `done/completed`.
- Focused assignment regression: 38 passed, 475 deselected.
- Full supervisor regression: 513 passed, 147 subtests passed in 56.73s.
- Python compile, evidence JSON parse, commit trailers, `git diff --check`, and exact-head GitHub CI are required before review handoff.
- Evidence manifest: `docs/deployment/evidence/supervisor/SUP-ASSIGNMENT-FALLBACK-ATOMIC-REBINDS-OPERATOR-V4-20260802/evidence.json`.

## Scope Boundary And Rollback

Owned scope is supervisor assignment planning/persistence, supervisor regression coverage, this task brief, and this task evidence directory. The task does not edit account groups, quota groups, provider policy, config, canonical JSON by hand, live runtime, services, deployments, or the 28-task product catalog. Rollback is a revert of the eventual V4 merge commit; no state or config migration is required.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
