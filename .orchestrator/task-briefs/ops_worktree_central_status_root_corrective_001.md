# Task Brief: OPS-WORKTREE-CENTRAL-STATUS-ROOT-CORRECTIVE-001

> Bootstrap workaround for this task itself: until the post-merge fix is
> installed, every owner or reviewer must run governed state, review, handoff
> and closeout commands through `/home/lupin/code/pantheon/scripts/ai-status.sh`
> with its own real identity (`AI_NAME=Codex2` for the owner or
> `AI_NAME=Antigravity` for the reviewer).
> Git, edits and tests remain in this task worktree. Verify the transition with
> the central `show` command.

## Problem proved on 2026-07-16

An isolated worker runs product commands in its task worktree, which is
correct. But `./scripts/ai-status.sh` also resolves state relative to that
worktree. In the guardrail repair worktree it read a 28KB stale
`ai-status.json` and a 41MB stale `ai-activity-log.jsonl`, while the live
coordination root held the current 995KB board and 2.8MB rotating log. The
owner's valid review handoff then timed out without reaching the central
board.

Archived task `OPS-WORKER-TREE-CWD-001` cannot prove this solved: it is marked
done with an empty acceptance list. Preserve that archive and create this
corrective task instead of reopening it.

## Required implementation

Owner Codex2 implements in a clean task worktree. Reviewer Antigravity reviews.
Keep git/build/test/product commands isolated to the task worktree, but route
all governed coordination commands (`show`, `note`, `start`, `progress`,
`handoff`, `blocker`, `approve`, `done`, archive/status log writes and locks)
to one explicit central coordination root supplied by the supervisor.

The root must be validated, absolute, repository-bound and fail closed when
missing, invalid, symlinked to another repository or inconsistent across
status, activity, archive and lock paths. Do not silently fall back to a stale
worktree copy in an auto worker. Interactive/local developer use may retain a
documented safe default when no supervisor binding is present.

Update the worker launch environment and prompts/wrappers so fleets do not
need to remember an ad-hoc absolute command. Do not change product source
working-directory isolation.

## Required tests and proof

Use two temporary repositories representing central root and task worktree.
Prove a worker launched in the task worktree:

- reads the central task and not a conflicting stale worktree task;
- writes `progress` and `handoff` only to central state/activity/locks;
- leaves worktree `ai-status.json`, activity log and archive byte-identical;
- cannot use a mismatched, missing, relative or symlinked coordination root;
- still runs ordinary git/test commands in the worktree;
- preserves concurrent writer locking and outbox recovery behavior.

Run the full worker runner, supervisor, runtime state and ai-status suites.
Deliver code, tests, runbook and exact regression evidence through a reviewed
PR to `dev`. Do not edit live coordination state by hand and do not claim the
runtime is repaired until the separate post-merge install task passes.

## Final merge gate

Keep auto-merge disabled. If `origin/dev` advances after any review starts,
the owner must compose the latest `origin/dev` into the task branch and rerun
the required suites on that final tree. Antigravity must then record approval
for that exact final head through the central status command. A reviewer note
or review-document commit that changes the PR head invalidates the preceding
exact-head approval; do not merge until the new head is named and approved.

## First implementation pre-review (2026-07-16)

Commit `7d3d01e2c` is an anchor only. Before opening or handing off a PR, the
owner must correct these gaps:

- a supplied root that points at a second, valid git repository with its own
  `ai-status.json` currently passes. Bind the supervisor-expected central root
  separately and reject this mismatch; add a two-valid-repository test;
- reject a root reached through any symlinked path component, not only when
  the final path itself is a symlink;
- extend integration coverage beyond one Codex2 `show/progress/handoff`.
  Exercise a distinct reviewer review/approve-or-reject transition and the
  owner final `done` archive transition. Prove the central archive/index,
  derived outputs and locks change as required while every corresponding
  worktree-local file remains byte-identical;
- run the adapter environment propagation and supervisor watchdog tests as
  well as the required worker/supervisor/runtime/ai-status suites. If the full
  supervisor suite has pre-existing failures, capture the same failures on
  exact `origin/dev` as baseline evidence;
- make `git diff --check` clean.

The live Claude review run for `LOOP-PROD-DONE-GUARDRAIL-REPAIR-001`
reproduced the defect: worktree-local `python3 scripts/ai_status.py show`
hung until the planner terminated only that read-only child. Owner and
reviewer paths are both in scope.
