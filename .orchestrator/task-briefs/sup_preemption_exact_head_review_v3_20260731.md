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

## Required Test Repair
- Root cause: the run-once planning-materialization test patched `supervisor.subprocess.run` globally, while the newly composed assistant dev-bridge inbox phase now validates the governed command root with `git rev-parse --show-toplevel`. The unrelated validation call was therefore captured by the planning materialization mock.
- Repair: stub `drain_assistant_dev_packet_inbox` to return `False` in this test, matching the existing isolation of unrelated run-once phases. The `subprocess.run` mock remains exclusive to the `planning_state.py materialize` contract and retains its exact-once assertion.
- Focused regression: `PYTHONPATH=.orchestrator .venv-pantheon/bin/python3 -m pytest -q .orchestrator/test_supervisor.py::RunOnceSupervisorStateTests::test_run_once_auto_materializes_accepted_session_before_execution_dispatch` passed (`1 passed`).
- Full focused suite: `PYTHONPATH=.orchestrator .venv-pantheon/bin/python3 -m pytest -q .orchestrator/test_supervisor.py .orchestrator/test_dispatch_policy.py` passed (`486 passed, 4 subtests passed`).
- Syntax and evidence checks: `python3 -m py_compile` passed for the four scheduler implementation/test files, and `python3 -m json.tool` passed for the existing task evidence manifest.
- Scope remains test-only beyond this task record: `.orchestrator/supervisor.py`, `.orchestrator/dispatch_policy.py`, `.orchestrator/test_dispatch_policy.py`, `.orchestrator/config.json`, and the reviewed evidence manifest are unchanged.
