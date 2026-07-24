# Task Brief: SUP-REVIEW-REDISPATCH-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Redispatch interrupted governed reviews exactly once
- Status: todo
- Owner: Codex2
- Reviewer: Antigravity
- Next: Assignment created

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

## Verification
- `python3 .orchestrator/test_supervisor.py` — 327 tests passed.
- `PYTHONPATH=.orchestrator /home/lupin/pantheon/.venv/bin/pytest -q .orchestrator/test_dispatch_policy.py`
  — 26 tests passed.
- `python3 -m py_compile .orchestrator/supervisor.py .orchestrator/dispatch_policy.py .orchestrator/test_supervisor.py .orchestrator/test_dispatch_policy.py`
  — passed.
- `git diff --check` — passed.
