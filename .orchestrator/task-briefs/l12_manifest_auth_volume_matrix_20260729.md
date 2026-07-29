# Task Brief: L12-MANIFEST-AUTH-VOLUME-MATRIX-20260729

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: L12 manifest auth volume applicability matrix workstream
- Status: review_approved
- Owner: Codex
- Reviewer: Antigravity
- Next: Review approved exact HEAD 96fdc27cf7d645dedfe9e62ddbebbce6d472e0d3: Verified 27-worker applicability matrix against bare Compose and source refs; audit mode passes with 17 auth pass/10 declared auth gaps, 27/27 volume pass, and 6 delegated zero-volume workers; default mode exits 1 with admission_ready=false as designed; verified pytest 6 passed; confirmed zero changes to docker-compose.yml, deploy script, parent evidence, or .orchestrator/config.json.

## Summary
建立每個 worker 的 auth 與 durable-volume applicability matrix，補 readback/validator 缺口。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
