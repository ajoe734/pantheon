# OPS-PROMOTE-CONFLICT-RECOVERY-001 — Recover the publish-to-master promote train

Priority: P1
Repository: `ajoe734/pantheon`
Merge target: `dev`
Owner: Codex2
Reviewer: Claude
Depends on: `OPS-DISPATCH-LEASE-SYNC-001`

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

## Delivery evidence (2026-07-22)

### Deterministic recovery behavior

The implementation landed on `dev` through PRs
[#3959](https://github.com/ajoe734/pantheon/pull/3959),
[#3960](https://github.com/ajoe734/pantheon/pull/3960),
[#3962](https://github.com/ajoe734/pantheon/pull/3962), and
[#3966](https://github.com/ajoe734/pantheon/pull/3966). The final merge commit
is `675953033132a861587fabdd6f13e53353128f53`.

`publish_promote.py` now reports every discovered candidate before selecting
work. Its stable dispositions distinguish legacy version formats,
superseded daily snapshots, releases already reachable from `master`, existing
promote PRs, eligible releases, and blocked/conflicted releases. A failed
candidate preflight is recorded and iteration continues, so the historical
`v2026.07.15.0` failure from
[run 29925995403](https://github.com/ajoe734/pantheon/actions/runs/29925995403)
cannot hide later candidates.

For common-history content conflicts and unrelated histories, promotion uses a
two-parent snapshot bridge: parent one is the current protected `master`,
parent two is the immutable release commit, and the commit tree is exactly the
release tree. This keeps the release reachable, makes a first-parent revert
restore the pre-promotion `master` tree, and prevents stale `master` fragments
from leaking into the promoted snapshot. The workflow uses ordinary branch
pushes and protected PR auto-merge; it does not force-push, write directly to
`master`, or mutate release tags.

### Candidate disposition and protected terminal

[Workflow run 29944823071](https://github.com/ajoe734/pantheon/actions/runs/29944823071)
completed the formerly failing backlog scan and emitted all 49 dispositions:
5 `legacy_format`, 43 `superseded`, and one `eligible`
(`v2026.07.22.2`). It opened protected promote PR
[#3965](https://github.com/ajoe734/pantheon/pull/3965).

The first protected smoke run on that PR exposed that an intermediate
release-wins textual merge retained an incompatible stale BFF fragment. The
final exact-snapshot implementation in #3966 replaced that strategy. The
promote branch was then advanced without force from `dd12c11bca12cceba47e61a720216495043d882b`
to correction commit `907e50448f86dc1b226323dfcd6fe4d600298be2`, whose tree is
the immutable release tree. Required checks passed:

- `Commit trailers` (17 seconds)
- `Runtime mirror guard` (7 seconds)
- `Smoke acceptance` (50 seconds)

Protected auto-merge completed #3965 as
`ff77abecf2c2c9fe819fd73e0a0be07eb0be3809`. Verification after the merge
showed:

- `master` tree: `ef2ef8a8108002f048b09650207a3a12d6879a8f`
- `release/v2026.07.22.2` tag object:
  `0df22a28c723a757aea467c6167fadfaa75e5a2b`
- peeled release commit: `52d9ed234af280fc239459bfeddf76886ae35f08`
- release tree: `ef2ef8a8108002f048b09650207a3a12d6879a8f`
- the release commit is an ancestor of the protected `master` merge commit
- successful `Master Release` run
  [29945654489](https://github.com/ajoe734/pantheon/actions/runs/29945654489)
  created `prod/v2026.07.22.2` at the protected merge commit

The release tag object was not changed during recovery. A final idempotency
dispatch against the merged implementation,
[run 29945824590](https://github.com/ajoe734/pantheon/actions/runs/29945824590),
classified `v2026.07.22.2` as `already_reachable: 1`, reported zero eligible
candidates, skipped the PR-opening step, and completed successfully.

### Backlog reconciliation

Only after #3965 reached its protected terminal state, the seven stale promote
PRs were closed as superseded: #2817, #2816, #2815, #2814, #2813, #2367, and
#1495. Each closeout comment cites #3965, the exact protected merge commit, and
the production tag evidence. No promote branch or release/production tag was
deleted.

### Focused verification

The task implementation was validated with:

```text
python3 -m unittest scripts/git/test_git_workflow_helpers.py
python3 -m py_compile scripts/git/publish_promote.py scripts/git/test_git_workflow_helpers.py
git diff --check
```

The focused suite runs 34 tests, including isolated Git fixtures for the
historical conflict, complete continue/report behavior, exact-tree bridge
parents and rollback, idempotency, protected auto-merge, and the absence of
force pushes. The repository environment did not have the optional `pytest`
module installed; the same test file ran through the standard-library unittest
runner, while GitHub smoke acceptance independently passed on the promoted
release tree.

### Owner finalization

Claude approved the task after independently re-running the focused 34-test
suite and checking the protected terminal evidence. Codex2 then composed the
review approval with the latest `origin/dev` and repeated the closeout checks:

- `python3 -m unittest scripts/git/test_git_workflow_helpers.py` — 34 tests,
  all passed.
- `python3 -m py_compile scripts/git/publish_promote.py scripts/git/test_git_workflow_helpers.py`
  — passed.
- `git diff --check` — clean.

The final readback still resolves protected `master` to
`ff77abecf2c2c9fe819fd73e0a0be07eb0be3809`, with both the master and release
trees at `ef2ef8a8108002f048b09650207a3a12d6879a8f`. The immutable release tag
object remains `0df22a28c723a757aea467c6167fadfaa75e5a2b`, its release commit remains
an ancestor of `master`, `prod/v2026.07.22.2` remains pinned to the protected
merge, run 29945824590 remains successful, and all seven reconciled historical
promote PRs remain closed. This is the owner closeout record for the approved
delivery; it does not expand the task's implementation or operational scope.
