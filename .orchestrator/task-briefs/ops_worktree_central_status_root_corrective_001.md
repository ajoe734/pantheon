# Task Brief: OPS-WORKTREE-CENTRAL-STATUS-ROOT-CORRECTIVE-001

> Bootstrap workaround for this task itself: until the post-merge fix is
> installed, Codex2 must run governed state/handoff commands through
> `/home/lupin/code/pantheon/scripts/ai-status.sh` with `AI_NAME=Codex2`.
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
