# OPS-PROMOTE-CONFLICT-RECOVERY-001 — Recover the publish-to-master promote train

Priority: P1
Repository: `ajoe734/pantheon`
Merge target: `dev`
Owner: Codex2
Reviewer: Codex

## Objective

Make `publish-promote.yml` handle the accumulated publish candidates without
stopping forever on the first historical conflict, while retaining protected
checks and rollback-safe promotion.

## Owned scope

- `scripts/git/publish_promote.py`
- publish/promote workflow and focused tests
- non-destructive backlog reconciliation evidence

## Required work

1. Reproduce the `v2026.07.15.0` conflict from run 29925995403 in an isolated
   repository fixture.
2. Define deterministic behavior for conflicted, superseded, already-reachable,
   and eligible candidates. One conflict must not hide the status of all later
   candidates.
3. Reconcile the seven open `promote/*` PRs to `master`; close only those proven
   superseded/reachable and never manually merge around protected checks.
4. Preserve hourly publish-cut behavior and prove that no duplicate release or
   missing accepted commit is introduced.

## Acceptance

- Focused workflow/script tests cover conflict, continue/report, idempotency,
  protected-check, and no-force-push behavior.
- The workflow produces a complete candidate disposition instead of an
  unhandled `git merge` exception.
- One eligible promotion reaches its protected terminal state, or the closeout
  names a specific external gate with all other candidates reconciled.
- Existing date-form release tags remain immutable.
- PR merges to `dev` and a subsequent scheduled/manual dry run is archived.

## Exclusions

- No direct push or force push to `master`.
- No deleting release tags or bypassing required checks.
