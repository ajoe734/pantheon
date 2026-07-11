# Review: MGMT-PERF-IA-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3

Reviewer: Claude
Date: 2026-07-11
Artifact reviewed: `support/sidecars/MGMT-PERF-IA-001/MGMT-PERF-IA-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md` (commit `ecb9ef46b`)

## Verdict

Reopen — the observed-state table is stale relative to the current live state
of both `MGMT-PERF-IA-001` and execute-plans PR #250, and this document's
entire purpose is to be an accurate closeout-gate snapshot.

## What the artifact claims

- `MGMT-PERF-IA-001` status: `review_approved`.
- execute-plans PR #250: `OPEN`, merge state `UNSTABLE`, `integration-gate`
  `IN_PROGRESS`.
- Implied remaining work: wait for the gate to finish, then merge and close
  out.

## What live state actually shows (checked via `ai-status.sh show`, not the
worktree mirror, per prior guidance that the worktree file can be stale)

```
AI_NAME=Claude ./scripts/ai-status.sh show MGMT-PERF-IA-001
```

- `status`: `blocked` (not `review_approved`).
- `owner`: `Claude`, `reviewer`: `Antigravity`, `waiting_for`: `Human/Ops`.
- `next`: execute-plans PR #250 integration-gate run 29143828567 now
  **FAILS** (not in-progress). The redirect of `/management/promotion-allocation`
  per `docs/04/pantheon_management_performance_ranking_ia_gap_2026-07-11/archive/ROUTE_MIGRATION_MATRIX.md`
  conflicts with `PPL-ALLOC-006` (owner Claude, `in_progress`, execute-plans
  PRs #249/#251 open), which is concurrently expanding the same live page
  (`src/management/pages/oversight/PromotionAllocation.tsx`). dev's own e2e
  suite still asserts the pre-migration live route. This is a genuine
  conflict between two canonical planning docs, not a rebase/flake issue,
  and needs a human/product IA-precedence decision before re-attempting
  merge. Full evidence trail: `.orchestrator/task-briefs/mgmt_perf_ia_001.md`.

## Why this matters

This packet exists specifically to give the parent owner an accurate
closeout-gate snapshot. "Integration-gate in progress" reads as "wait for
CI," which understates the actual situation: the gate is failing on a
genuine cross-task IA-precedence conflict that requires an explicit human
decision (sequence `MGMT-PERF-IA-001` behind `PPL-ALLOC-006`, or amend the
Route Migration Matrix) before the PR can be re-attempted. A reader relying
on this sidecar alone would not know escalation to Human/Ops is required.

## Requested change

Refresh the Observed State table (and the "Required interpretation" /
Parent Closeout Gate sections as needed) to state:

1. `MGMT-PERF-IA-001` is `blocked`, waiting on `Human/Ops`, not
   `review_approved`.
2. execute-plans PR #250 integration-gate currently `FAILS` due to a real
   conflict with `PPL-ALLOC-006`'s concurrent expansion of
   `PromotionAllocation.tsx`, not merely "in progress."
3. The parent closeout gate cannot proceed until a human/product
   IA-precedence decision is made and the PR is re-attempted and merged;
   this sidecar should not imply the gate is a matter of waiting for CI to
   finish.

Scope of the fix stays within this same support artifact — no canonical,
BFF runtime, schema, registry, governance, or frontend changes are needed
to correct this.
