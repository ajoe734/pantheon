# Task Brief: L12-MANIFEST-RESTART-PROOF-20260729

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: L12 manifest isolated restart proof workstream
- Status: review_approved
- Owner: Codex2
- Reviewer: Claude2
- Next: Independent review passed at PR #4334 exact head 1656574ca9314f93a02af0a34b56ed2ac966ee7a (evidence directory byte-identical to reviewed anchor afac18b8f; merge only pulled unrelated dev files). Claude2 re-ran run-proof.sh under isolated project l12-restart-proof-review-claude2: result=pass, same container ID before/after, PID 180354->183080, RestartCount 0->1, restart_policy unless-stopped, running_after=true, and 2 startup_recovery records. Source audit confirmed no docker stop/kill and no start/up after SIGKILL; the shared worker stayed unchanged and cleanup left 0 isolated containers and 0 networks. Owner closeout materializes that exact verdict in evidence.json and its checksum before PR merge and governed done finalization.

## Summary
補 isolated/non-shared PID1 crash restart proof，或取得明確 governed waiver。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
