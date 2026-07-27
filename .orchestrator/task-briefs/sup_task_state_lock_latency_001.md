# Task Brief: SUP-TASK-STATE-LOCK-LATENCY-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Bound supervisor task-state lock latency and projection truth
- Status: todo
- Owner: Claude
- Reviewer: Codex2
- Next: Extend the live reproduction beyond the prior 669s loop. Supervisor PID 901543 tick heartbeat 22:35:04Z completed 22:47:55Z (771s) while reviewer/status processes queued on runtime-admission inode 807896; the task-state reviewer reopen waited about 9 minutes. The next exclusive hold was observed from at least 22:50:27Z until 22:59:04Z (~517s); Codex2 BFF reopen PID 480495 waited from about 22:51:17Z and committed only at 22:59:45Z. A verifier begun around the 22:48 lock handoff transiently reported event_count=2046 with expected SHA from event 2045 and projected SHA from event 2046 while the concurrent append landed, then a stable rerun at event 2049 returned ok=true; cover this lock-domain/snapshot race explicitly. Even lock-free Human/Ops note commands over the ~157MB/2050-event journal each required roughly 55-90s. Preserve these measurements in the regression fixture; target p95 <2s without lock bypass, config edit, or worker kill.

## Summary
縮短 supervisor task-state/runtime-admission 鎖持有時間，修正 caught_up 語意，讓 approve/assign 與 heartbeat 不再被數分鐘 projection 阻塞。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
