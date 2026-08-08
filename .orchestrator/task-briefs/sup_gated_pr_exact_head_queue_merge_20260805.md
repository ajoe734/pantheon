# SUP-GATED-PR-EXACT-HEAD-QUEUE-MERGE-20260805

Status: proposed
Owner: Claude
Reviewer: Human/Ops
Depends on: SUP-MERGE-QUEUE-AWARE-INTEGRATOR-20260804 (#4549)

## Problem

`dev`'s `strict: true` required-status-check setting means any open PR goes
`BEHIND` the moment another PR lands, and cannot merge until it is current
again. At the observed merge rate (45 commits landed in roughly the two
hours preceding this task), a PR without someone actively re-syncing it
never catches up -- confirmed live: 16 open PRs sat `BEHIND` simultaneously,
one (`task/SUP-TASK-FAILURE-STREAK-SCHEMA-20260804`, PR #4564) resynced
against `origin/dev` twice in 46 minutes and was still `BEHIND` afterward.

GitHub's own merge queue would fix this, but is unavailable: the repository
is owned by a personal ("User") account, and merge-queue rulesets are an
organization-only feature (confirmed live -- a `merge_queue` ruleset rule is
rejected while a `deletion` rule on the same endpoint succeeds, isolating
the rejection to the feature itself, not a payload error).

For `review_before_merge` (gated) PRs specifically, the standard fix people
reach for -- rebase onto the latest base and push -- is not available at
all: rebasing rewrites the head, and `task_review_merge_gate.py`'s exact-
head approval binding means a rewritten head was never reviewed. Before this
task, a gated PR that fell behind just waited indefinitely for someone to
refresh the branch and get a fresh review, even when the actual content
never overlapped with whatever moved on `dev`.

## Fix

A home-grown, narrower substitute for what GitHub's merge queue does: prove
the combination is safe immediately before merging, without ever moving the
reviewed commit.

In `run_rebase_smoke()`'s gated branch, when the approved head does not yet
contain the current `dev` tip: build a disposable worktree at the exact
approved head (unchanged), `git merge --no-edit origin/dev` into it (never
pushed anywhere), and run the configured smoke commands against that
ephemeral result.

- Clean merge + smoke passes -> `"exact_head_verified_clean"`.
  `integrate_candidate()` now treats this as equivalent to "already
  current" for the final `mergeStateStatus` gate and proceeds to the real
  merge via `gh pr merge --merge --match-head-commit <approved-head>` --
  the same call already used for the "already current" case, still binding
  to the exact reviewed SHA.
- Real conflict -> `"exact_head_merge_conflict"`, `git merge --abort`ed
  immediately, task blocked with an explicit "real conflict, not just
  staleness" reason (an unblock task is opened) -- this is the one case
  that genuinely still needs the owner, unchanged from before.

This is intentionally narrower than GitHub's merge queue: no batching of
multiple queued PRs, no shared build cache, one PR verified and merged at a
time via the existing `.orchestrator/auto-integrator.lock` serialization.
It only replaces the one property that mattered here -- "was this proven
safe against the current base right before landing" -- without requiring
the PR branch itself to be rewritten to prove it.

## Depends on turning off `dev`'s `strict: true` (separate step)

This fix alone is necessary but not sufficient: `gh pr merge
--match-head-commit` still goes through GitHub's own branch protection,
which independently enforces `strict: true` regardless of what this
process verified locally. That setting is being turned off as a follow-up
step once this lands and `dev-root` picks it up -- turning it off first
would have been safe too (the old integrator code simply keeps treating
`BEHIND` as `waiting`, no regression either order), but landing the code
that can actually use the relaxed setting first keeps the two changes
easy to reason about independently.

None of the seven regressions `task_review_merge_gate.py` was built to
prevent (2026-07-26, PRs #4212-#4230) were about staleness -- they were all
about merging without genuine independent review. Turning off `strict` does
not touch that: review is still required, the exact-head binding is still
enforced, only the "must be byte-for-byte current" requirement is relaxed.

## Test plan

- `pytest scripts/git/test_task_review_merge_gate.py` -- new: clean
  ephemeral merge lands the exact head; a real conflict blocks with an
  explicit reason and never force-pushes; two pre-existing tests updated
  to the new (correct) "merges via the queue path" outcome instead of the
  old "waits forever" one, with their original safety-property assertions
  (never rebased, never force-pushed) preserved
- `pytest scripts/git/test_auto_integrator.py
  scripts/git/test_git_workflow_helpers.py scripts/git/test_task_pr_triage.py
  scripts/git/test_github_review_bridge.py
  scripts/git/test_canonical_review_gate_workflow.py` -- 221 total, zero
  regressions
- `pytest scripts/test_ai_status.py .orchestrator/test_github_bus.py` --
  unaffected
- Live, non-mocked git verification before writing any tests: a real
  disposable-repo clean merge (confirms the reviewed branch ref is
  byte-for-byte unchanged after merging in unrelated upstream commits) and
  a real conflicting merge (confirms detection and clean `--abort`
  recovery) -- both against real git, not `FakeRunner`.
