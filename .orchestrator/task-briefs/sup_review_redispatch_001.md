# Task Brief: SUP-REVIEW-REDISPATCH-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Redispatch interrupted governed reviews exactly once
- Status: review_approved
- Owner: Codex2
- Reviewer: Antigravity
- Next: Owner closeout after the reviewed implementation merged through PR #4022 and the focused verification was rerun.

## Summary
修 reviewer worker 在 supervisor recovery／runtime 更新中止後，seen event 已消耗卻不會重派的缺陷；用 task/handoff 狀態與 terminal worker evidence 做一次性、隔離 worktree 的 governed redispatch。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

## Implementation Evidence
- An unchanged `review` task may bypass the normal seen-signature cooldown only
  when its assigned reviewer still has a pending handoff and the matching prior
  review worker is terminal.
- The terminal worker records the consumed event signature after the recovery
  event is durably queued. A worker launched by that recovery event carries its
  parent lineage and cannot recursively trigger another governed redispatch.
- The recovery event explicitly requires an isolated task worktree. Shared-root
  fallback remains fail-closed, and a dirty reused worktree remains blocked by
  the existing refresh and tree guards.
- Review recovery does not write a synthetic task note or change the existing
  handoff. The reviewer must still use the governed `approve` or `reopen`
  transition.

## Reviewer Approval
- Antigravity approved the task at `2026-07-24T01:12:15Z`.
- Review evidence: “Reviewed implementation & tests for
  SUP-REVIEW-REDISPATCH-001: PR #4022 (commit `ddd08be87` merged into task
  branch tip `86ff5fb2a`) correctly resolves reviewer worker redispatch on
  supervisor recovery/runtime replacement without signature cooldown
  deadlocks, forcing an isolated worktree and preserving handoff truth.
  Verified test_supervisor (327 tests), test_dispatch_policy (26 tests),
  py_compile, and git diff.”

## Delivery Evidence
- Anchor commit: `e39eeb77ea6bfbef52360a68e07ac04fd47a49e9`.
- Reviewed implementation/test commit:
  `ddd08be8781c6b97eefca012d80f405338a21ba7`.
- Task PR: `https://github.com/ajoe734/pantheon/pull/4022`.
- GitHub refreshed the task branch to
  `73fb927a19a83f2678150c1867c3cba6df041846` before merging it into `dev` as
  `cb31573beeafc4771a196335bb554bfbd9b1a0e0`. Both the reviewer-observed
  integrated tip and the merged PR head contain the reviewed implementation
  commit.
- Required Branch CI checks passed for PR #4022: Commit trailers, Runtime
  mirror guard, and Smoke acceptance.

## Verification
- `python3 .orchestrator/test_supervisor.py` — 327 tests passed.
- `PYTHONPATH=.orchestrator /home/lupin/pantheon/.venv/bin/pytest -q .orchestrator/test_dispatch_policy.py`
  — 26 tests passed.
- `python3 -m py_compile .orchestrator/supervisor.py .orchestrator/dispatch_policy.py .orchestrator/test_supervisor.py .orchestrator/test_dispatch_policy.py`
  — passed.
- `git diff --check` — passed.
