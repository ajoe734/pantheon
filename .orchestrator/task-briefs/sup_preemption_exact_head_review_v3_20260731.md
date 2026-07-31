# Task Brief: SUP-PREEMPTION-EXACT-HEAD-REVIEW-V3-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Re-review updated scheduler exact head after required base merge
- Status: in_progress
- Owner: Codex2
- Reviewer: Antigravity
- Next: .orchestrator/test_supervisor.py::RunOnceSupervisorStateTests::test_run_once_auto_materializes_accepted_session_before_execution_dispatch failed in test_supervisor.py (AssertionError: Expected 'run' to have been called once. Called 2 times; subprocess.run captured git rev-parse call from helper). Please repair mock expectations in test_supervisor.py and return task for re-review.

## Summary
GitHub required #4399 to update from dev after #4397 merged. This fresh independent Antigravity review binds the newly created exact head before root freeze and merge.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
