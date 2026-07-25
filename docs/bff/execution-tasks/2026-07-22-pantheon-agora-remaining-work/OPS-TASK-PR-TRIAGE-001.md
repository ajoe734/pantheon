# OPS-TASK-PR-TRIAGE-001 — Evidence-based overdue PR and task-branch triage

Priority: P2
Repository: `ajoe734/pantheon`
Merge target: `dev`
Owner: Antigravity
Reviewer: Codex
Depends on: `OPS-DISPATCH-LEASE-SYNC-001`

## Objective

Classify overdue task PRs and no-open-PR task branches so active work is visible
and superseded work can be safely retired without blanket deletion.

## Current evidence

- 29 open task PRs target `dev`: 18 `BEHIND`, 11 `DIRTY`, 4 draft.
- 2,089 remote `task/*` branches; approximately 2,060 have no open task PR.
- Historical archive delivery metadata contains known stale `ahead` values and
  cannot be used as deletion authority by itself.

## Owned scope

- read-only inventory/triage tooling and generated report
- PR labels/comments/closures for demonstrably superseded work
- retention/reachability contract and dry-run deletion manifest

## Required work

1. Join branch head, current `dev` reachability, open/closed/merged PR history,
   task archive, last update, and evidence references.
2. Classify each item as active-repair, conflict-needs-owner, merged-reachable,
   superseded, abandoned-unproven, or protected-retain.
3. Close superseded open PRs with the replacement/merge evidence.
4. Produce a deletion dry run only for branches proven merged/reachable or
   explicitly abandoned under the retention contract.

## Acceptance

- Every one of the 29 open PRs has an evidence-backed disposition and owner.
- #3936/#3948 resolve to the canonical lease repair chosen by
  `OPS-DISPATCH-LEASE-SYNC-001`; Agora #3058 is checked against later delivery.
- Dry-run results are deterministic and exclude recent, protected, open-PR,
  unrecoverable, or uncertain branches.
- No branch is deleted by this task.
- Tool tests and report validation pass; PR merges to `dev`.

## Exclusions

- No blanket close or branch deletion.
- No reliance on stale archive `ahead` metadata without GitHub/reachability
  verification.

## Closeout record

- [Owner finalization and accepted delivery evidence](../../../04/pantheon_agora_remaining_work_2026-07-22/archive/OPS-TASK-PR-TRIAGE-001-closeout-2026-07-22.md)
