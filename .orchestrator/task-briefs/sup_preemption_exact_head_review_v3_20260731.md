# Task Brief: SUP-PREEMPTION-EXACT-HEAD-REVIEW-V3-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Re-review updated scheduler exact head after required base merge
- Status: in_progress
- Owner: Codex
- Reviewer: Antigravity
- Next: Human/Ops reopened the task to rebase PR #4413 onto current `origin/dev`, preserve the accepted scheduler/canary evidence semantics, rerun validation and checks, push, then hand off the new exact head to Antigravity. Do not alter config.

## Summary
GitHub required #4399 to update from dev after #4397 merged. This fresh independent Antigravity review binds the newly created exact head before root freeze and merge.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

## Required Test Repair
- Root cause: the run-once planning-materialization test patched `supervisor.subprocess.run` globally, while the newly composed assistant dev-bridge inbox phase validates the governed command root with `git rev-parse --show-toplevel`. The unrelated validation call was therefore captured by the planning materialization mock.
- Repair: stub `drain_assistant_dev_packet_inbox` to return `False` in this test, matching the existing isolation of unrelated run-once phases. The `subprocess.run` mock remains exclusive to the `planning_state.py materialize` contract and retains its exact-once assertion.
- Scope remains test-only beyond this task record: `.orchestrator/supervisor.py`, `.orchestrator/dispatch_policy.py`, `.orchestrator/test_dispatch_policy.py`, `.orchestrator/config.json`, and the reviewed evidence manifest are unchanged.

## Current-Dev Rebase Refresh
- Human/Ops reopened the task at `2026-07-31T17:30:54Z`; the earlier approval does not authorize merging a refreshed head.
- The three task commits rebased without conflicts onto `origin/dev` `dc5136394eb1041ceea1dcc066e55ac2179ca0e5` in a clean isolated worktree.
- The rebased delta remains limited to this task brief and the three-line `test_supervisor.py` mock-isolation fix; `.orchestrator/config.json` and the scheduler implementation are unchanged.
- `PYTHONPATH=.orchestrator .venv-pantheon/bin/python3 -m pytest -q .orchestrator/test_supervisor.py .orchestrator/test_dispatch_policy.py` passed (`490 passed, 4 subtests passed`).
- `python -m py_compile` passed for the four scheduler implementation/test files; commit trailers, evidence JSON, and `git diff --check` also passed.
- After push, Antigravity must independently review and bind the new PR #4413 exact head. Human/Ops root-freeze authorization remains separate and head-specific.
