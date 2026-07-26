# Task Brief: L12-ALPHA-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Dispatch approved Alpha replication into authoritative ExperimentRuns
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Review requires two acceptance fixes despite 58 scoped tests passing: (1) AlphaReplicationQueue.replay_dlq only compares last_replay_id, so an older replay ID is accepted again after another replay. Independent ABA repro produced first_A=True, B=True, reused_A=True, replay_count=3. Persist all consumed replay IDs (tenant/spec scoped) and add an A-B-A regression proving duplicate replay is always a no-op. (2) AlphaRevalidationWorker stores/returns nested domain etask/erun IDs and ReplicationController publishes research-authority evidence refs from them, while the real service GET routes are keyed by AuthoritativeTaskReceipt.authority_task_id and AuthoritativeRunReceipt.authority_run_id. Independent transport repro produced domain_run_id=erun-alpha-tenant-a-spec-a-attempt-1 and authority_run_id=rrun-1. Carry resolvable authority IDs through queue acknowledgement, controller result, and evidence refs, and add a real FastAPI test that dereferences the emitted task/run references. Update evidence after the fixes.

## Summary
統一 StrategySpec identifier，限制 approved review 才能進 queue，補 tenant、lease、DLQ/replay，將結果寫入真實 research authority 而非 stub/local run。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
