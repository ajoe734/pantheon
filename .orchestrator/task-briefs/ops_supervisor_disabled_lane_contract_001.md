# Task Brief: OPS-SUPERVISOR-DISABLED-LANE-CONTRACT-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Define disabled supervisor lane contract
- Status: review_approved
- Owner: Antigravity
- Reviewer: Claude
- Next: Verified: PR #3771 (d2c6cbceb) merged into dev. Diff matches all 6 required behaviors (disabled-lane zero-capacity assertions, worker_os_duplicate_guard mock for synthetic roster, new disabled-agent sidecar-exclusion test). Ran hermetic PANTHEON_STATUS_ROOT=/tmp: test_supervisor.py 277/277 pass, test_runtime_state+test_supervisor_watchdog+test_worker_runner_heartbeat 82/82 pass, py_compile OK, git diff --check clean. Live Codex process confirmed present during run (post-merge replay requirement met). No production duplicate-worker guard tests touched. Approved.

## Summary
-

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
