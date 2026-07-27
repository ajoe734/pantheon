# Task Brief: SUP-TASK-STATE-LOCK-LATENCY-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Bound supervisor task-state lock latency and projection truth
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Review failed after PRs #4239/#4250/#4253 (all merged/checks green). AC1 is still false: _run_once_locked invokes maybe_auto_commit_archive under the exclusive runtime-admission lock; that waits up to 180s while auto_commit_archive.py performs git fetch and task_finalize push/PR network work. The same locked cycle invokes reconcile_ownerless_in_progress_tasks, whose squash path calls _merged_pull_requests_for_branch -> run_gh_process with a 20s network timeout. Move archive and merged-PR lookup network work to prelock snapshots or deferred postlock execution with freshness/fail-closed apply gates, and add regressions proving those subprocesses run only outside runtime_state_lock. Deferred termination must also fail closed when worker_pid_start_ticks is unavailable and must not report termination complete before post-lock confirmation; current expected_start_ticks=None treats any reused PID as alive and can escalate against it. AC3 evidence is not end-to-end: task_state_lock_latency_bench.py runs synthetic _reconcile_once/current_load_state/current_commit with append withheld, never actual governed approve/assign/note/reviewer commands or the full run_once runtime-admission path. Add scratch-root concurrent governed-command coverage against a real/full supervisor cycle and refresh evidence. Independent focused validation: 108 relevant tests passed; bash helper syntax and git diff --check passed, but those suites do not cover these defects.

## Summary
縮短 supervisor task-state/runtime-admission 鎖持有時間，修正 caught_up 語意，讓 approve/assign 與 heartbeat 不再被數分鐘 projection 阻塞。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
